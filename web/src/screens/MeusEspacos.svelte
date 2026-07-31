<script lang="ts">
  // The Genie Space promotion surface, built as a DOSSIER INDEX (see the direction contract in
  // App.svelte).
  //
  // S3 (D3/GR3) merged the old "Meus espaços" grid and "Minhas promoções" list into one
  // space-grouped page. This redesign keeps that merge and changes the STRUCTURE: instead of a grid
  // of same-size cards that reveal nothing until clicked, every Space is a register entry whose
  // standing — in flight, who holds it, how many prior promotions, when it last moved — is readable
  // on the index itself.
  //
  // The register grammar (DossierIndex / DossierRow / DossierToolbar / DossierEmpty) is SHARED with
  // the painéis page, so an author learns the flow once. Only the vocabulary and the per-kind
  // evidence differ: a Space is described by its audience and benchmarks, never by datasets/widgets.
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import DossierIndex from '../lib/components/DossierIndex.svelte';
  import DossierRow from '../lib/components/DossierRow.svelte';
  import DossierToolbar from '../lib/components/DossierToolbar.svelte';
  import DossierEmpty from '../lib/components/DossierEmpty.svelte';
  import PromotionList from '../lib/components/PromotionList.svelte';
  import PromotionConfirm from '../lib/components/PromotionConfirm.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import FlowSteps from '../lib/components/FlowSteps.svelte';
  import { getResources, getPromotions, isAuthError, type PromotionSummary } from '../lib/api';
  import { statusBucket, type StatusBucket } from '../lib/status';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { PromotableResource, Whoami } from '../lib/types';

  interface Props {
    promotion: Promotion;
    who: Whoami | null;
    /** The dev workspace host (G5) — threaded down to PromotionReview's RehydrateAction. */
    devHost?: string | null;
    /** W3: the prod workspace host — threaded down to PromotionReview's "Abrir Genie em produção". */
    prodHost?: string | null;
    /** Open a promotion's detail (deep-link `#/promocoes/:id`). */
    onOpenPromotion: (summary: PromotionSummary) => void;
  }
  let { promotion, who, devHost = null, prodHost = null, onOpenPromotion }: Props = $props();

  // The user's promotable GENIE SPACES (OBO). In $state so an error is retryable.
  //
  // Filtered to one kind on purpose: AI/BI dashboards have their own destination (`#/paineis`).
  let resourcesP = $state(loadSpaces());
  function loadSpaces(): Promise<PromotableResource[]> {
    return getResources().then((all) => all.filter((r) => r.kind === 'genie_space'));
  }

  // History is best-effort — a fetch failure here must never block the primary "start a promotion"
  // flow (the register still needs to render). Admins may load ALL promotions (LB5's scope toggle).
  let scope = $state<'mine' | 'all'>('mine');
  function loadPromotions(s: 'mine' | 'all'): Promise<PromotionSummary[]> {
    return getPromotions(s).catch(() => []);
  }
  let promotionsP = $state(loadPromotions('mine'));
  // After the initial await, refresh history in the background as the active run advances. Keeping
  // this separate from `promotionsP` avoids replacing the whole workspace with skeletons on every
  // phase transition while still making the newly-created run/count immediately discoverable.
  let refreshedPromotions = $state<PromotionSummary[] | null>(null);
  let historyRefreshKey = '';
  function setScope(s: 'mine' | 'all'): void {
    scope = s;
    refreshedPromotions = null;
    historyRefreshKey = '';
    promotionsP = loadPromotions(s);
  }

  // `promotion` is the single source of truth for the selection — no parallel local state.
  const reload = () => {
    resourcesP = loadSpaces();
    refreshedPromotions = null;
    historyRefreshKey = '';
    promotionsP = loadPromotions(scope);
    promotion.select(null); // a fresh list invalidates any prior (possibly gone) selection
  };

  // G3: choosing a record only SELECTS the space (select() resets prior verdict state) — it does NOT
  // fire the request. The next step is the confirmation panel, bound to the chosen space, where the
  // Requester may optionally declare access before actually requesting the promotion (each space
  // still promotes on its OWN branch/PR — see app_logic.space_slug). D7: if the space already has an
  // open Promotion, the backend's per-slug idempotency folds this request into that SAME Promotion.
  function chooseSpace(resource: PromotableResource): void {
    promotion.select(resource);
  }

  // A space is picked but not yet requested — the register locks (only "← Escolher outro recurso" in
  // the confirmation panel can change the selection) while that step is on screen.
  const confirming = $derived(!!promotion.resource && promotion.phase === 'idle');

  interface EspacoGroup {
    resource: PromotableResource;
    /** Newest first. */
    promotions: PromotionSummary[];
  }

  function groupBySpace(resources: PromotableResource[], promotions: PromotionSummary[]): EspacoGroup[] {
    const byResource = new Map<string, PromotionSummary[]>();
    for (const p of promotions) {
      const list = byResource.get(p.resource_id);
      if (list) list.push(p);
      else byResource.set(p.resource_id, [p]);
    }
    for (const list of byResource.values()) {
      list.sort((a, b) => b.created_at.localeCompare(a.created_at));
    }
    const seen = new Set<string>();
    const groups: EspacoGroup[] = [];
    for (const r of resources) {
      groups.push({ resource: r, promotions: byResource.get(r.id) ?? [] });
      seen.add(r.id);
    }
    // A promotion whose resource isn't in the caller's CURRENT dev list (removed/renamed since) —
    // still surface its history rather than silently dropping it, using the promotion's own title.
    //
    // Kind-filtered: `getPromotions` returns EVERY kind, so without this a promoted dashboard would
    // appear in the Genie register (it has no matching entry in the genie_space resource list, so it
    // fell through to here). Painéis have their own destination.
    for (const [rid, list] of byResource) {
      if (seen.has(rid)) continue;
      const first = list[0];
      if (first.resource_kind !== 'genie_space') continue;
      groups.push({
        resource: { id: rid, title: first.resource_title ?? rid, kind: first.resource_kind },
        promotions: list,
      });
    }
    return groups;
  }

  // Pinned-first sort (GR3): a space with a currently-open promotion floats to the top; the rest
  // sort by most-recently-promoted; never-promoted spaces (empty history) sort last.
  function sortGroups(groups: EspacoGroup[]): EspacoGroup[] {
    return [...groups].sort((a, b) => {
      const aActive = activePhaseFor(a.resource.id);
      const bActive = activePhaseFor(b.resource.id);
      const aOpen = aActive ? !TERMINAL_PHASES.has(aActive) : a.promotions.some((p) => !p.terminal);
      const bOpen = bActive ? !TERMINAL_PHASES.has(bActive) : b.promotions.some((p) => !p.terminal);
      if (aOpen !== bOpen) return aOpen ? -1 : 1;
      const aDate = a.promotions[0]?.created_at ?? '';
      const bDate = b.promotions[0]?.created_at ?? '';
      if (!aDate && !bDate) return 0;
      if (!aDate) return 1;
      if (!bDate) return -1;
      return bDate.localeCompare(aDate);
    });
  }

  const STATUS_FILTERS: { key: StatusBucket | 'all'; label: string }[] = [
    { key: 'all', label: 'Todas' },
    { key: 'open', label: 'Em andamento' },
    { key: 'merged', label: 'Merged' },
    { key: 'failed', label: 'Falhou' },
    { key: 'deployed', label: 'Implantada' },
  ];
  let statusFilter = $state<StatusBucket | 'all'>('all');
  let searchQuery = $state('');

  function matchesFilter(group: EspacoGroup): boolean {
    if (!group.resource.title.toLocaleLowerCase('pt-BR').includes(searchQuery.trim().toLocaleLowerCase('pt-BR'))) {
      return false;
    }
    if (statusFilter === 'all') return true;
    const phase = activePhaseFor(group.resource.id) ?? group.promotions[0]?.current_phase;
    return !!phase && statusBucket(phase) === statusFilter;
  }

  let expandedIds = $state<Set<string>>(new Set());
  function toggleExpanded(id: string): void {
    const next = new Set(expandedIds);
    next.has(id) ? next.delete(id) : next.add(id);
    expandedIds = next;
  }

  const TERMINAL_PHASES = new Set(['deployed', 'deploy_failed', 'revision_mismatch', 'closed']);
  function activePhaseFor(resourceId: string): string | null {
    if (!promotion.initiatedHere || promotion.resource?.id !== resourceId) return null;
    return promotion.liveStatus?.phase
      ?? (promotion.phase === 'reviewing' ? 'checks_running' : promotion.pr ? 'open' : null);
  }

  $effect(() => {
    const id = promotion.promotionId;
    if (!promotion.initiatedHere || !id) return;
    const key = `${scope}:${id}:${promotion.liveStatus?.phase ?? promotion.phase}`;
    if (key === historyRefreshKey) return;
    historyRefreshKey = key;
    void loadPromotions(scope).then((rows) => {
      if (historyRefreshKey === key) refreshedPromotions = rows;
    });
  });
</script>

<div class="author-home">
  <header class="page-head">
    <h1>Preparar promoção</h1>
    <p>
      Escolha o Space; o app prepara um rascunho com revisão automática. O Responsável Técnico revisa
      e promove no GitHub.
    </p>
  </header>

  <!-- The flow is explained once, above the register: the author is here a few times a month and
       needs the governance sequence re-established each visit. -->
  <FlowSteps />

  {#await Promise.all([resourcesP, promotionsP])}
    <div class="workspace workspace--loading">
      <div class="stack"><Skeleton height="4rem" /><Skeleton height="4rem" /><Skeleton height="4rem" /></div>
      <Skeleton height="24rem" />
    </div>
  {:then [resources, promotions]}
    {@const currentPromotions = refreshedPromotions ?? promotions}
    {@const allGroups = sortGroups(groupBySpace(resources, currentPromotions))}
    {@const shownGroups = allGroups.filter(matchesFilter)}
    {@const selectedGroup = allGroups.find((group) => group.resource.id === promotion.resource?.id)}

    <DossierToolbar
      bind:query={searchQuery}
      searchLabel="Buscar por nome do Space"
      searchName="Buscar Space"
      countLabel={`${resources.length} Spaces disponíveis em Dev`}
    >
      {#snippet controls()}
        <label class="filter">
          <span class="visually-hidden">Filtrar por status</span>
          <select bind:value={statusFilter} aria-label="Filtrar por status">
            {#each STATUS_FILTERS as filter (filter.key)}
              <option value={filter.key}>{filter.label}</option>
            {/each}
          </select>
        </label>
        {#if who?.is_admin}
          <div class="scope" role="group" aria-label="Escopo do histórico">
            <Button variant={scope === 'mine' ? 'primary' : 'outline'} onclick={() => setScope('mine')}>Minhas</Button>
            <Button variant={scope === 'all' ? 'primary' : 'outline'} onclick={() => setScope('all')}>Todas</Button>
          </div>
        {/if}
      {/snippet}
    </DossierToolbar>

    {#if resources.length === 0 && allGroups.length === 0 && !who?.is_admin}
      <DossierIndex label="Spaces disponíveis" identHeader="Genie Space">
        <DossierEmpty
          icon="grid"
          title="Nenhum Genie Space encontrado"
          hint="Crie um no Genie nativo do workspace de dev — depois ele aparece aqui para promoção."
        />
      </DossierIndex>
    {:else}
      {@const recordOpen = promotion.opening || confirming || promotion.initiatedHere}
      <div class="workspace" class:workspace--open={recordOpen}>
        <DossierIndex label="Spaces disponíveis" identHeader="Genie Space">
          {#if shownGroups.length === 0}
            <DossierEmpty
              icon="grid"
              title="Nenhum Genie Space encontrado"
              hint="Nenhum espaço corresponde à busca e ao status selecionados."
            />
          {:else}
            {#each shownGroups as group (group.resource.id)}
              {@const activePhase = activePhaseFor(group.resource.id)}
              {@const latest = group.promotions[0] ?? null}
              <DossierRow
                resource={group.resource}
                icon="grid"
                kindLabel="Genie Space"
                promotions={group.promotions}
                expanded={expandedIds.has(group.resource.id)}
                onToggle={() => toggleExpanded(group.resource.id)}
                onPromote={chooseSpace}
                onOpenLatest={latest ? () => onOpenPromotion(latest) : undefined}
                busy={promotion.phase === 'reviewing' && promotion.resource?.id === group.resource.id}
                disabled={promotion.phase === 'reviewing' && promotion.resource?.id !== group.resource.id}
                selected={promotion.resource?.id === group.resource.id}
                {activePhase}
                activeRequester={activePhase ? promotion.requesterEmail : null}
                activeTerminal={activePhase ? TERMINAL_PHASES.has(activePhase) : false}
                activePhaseLoading={promotion.initiatedHere
                  && promotion.resource?.id === group.resource.id
                  && promotion.waitingForLiveStatus}
              >
                {#snippet history()}
                  <PromotionList promotions={group.promotions} onOpen={onOpenPromotion} />
                {/snippet}
              </DossierRow>
            {/each}
          {/if}
        </DossierIndex>

        <!-- Rendered only when a record is actually open: an always-present "Escolha um Space"
             placeholder cost half the viewport to say nothing, and the register itself already
             invites the choice. -->
        {#if recordOpen}
          <section class="working-panel" aria-live="polite">
            {#if promotion.opening}
              <div class="working-panel__loading" aria-label="Carregando promoção selecionada">
                <Skeleton height="3.5rem" />
                <Skeleton height="10rem" />
                <Skeleton height="16rem" />
              </div>
            {:else if confirming}
              {#key promotion.selectionSeq}
                <PromotionConfirm
                  {promotion}
                  promotions={selectedGroup?.promotions ?? []}
                  {onOpenPromotion}
                  onCancel={() => promotion.select(null)}
                />
              {/key}
            {:else}
              <PromotionReview {promotion} userEmail={who?.email ?? null} {devHost} {prodHost} />
            {/if}
          </section>
        {/if}
      </div>
    {/if}
  {:catch err}
    <div class="error-state" role="alert">
      <span class="error">
        {#if isAuthError(err)}Sessão expirada — recarregue a página para reautenticar.
        {:else}Não foi possível listar os espaços: {err instanceof Error ? err.message : String(err)}{/if}
      </span>
      {#if isAuthError(err)}<Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
      {:else}<Button variant="outline" onclick={reload}>Tentar novamente</Button>{/if}
    </div>
  {/await}
</div>

<style>
  .author-home {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .page-head h1 {
    font-size: clamp(1.25rem, 2vw, 1.5rem);
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .page-head p {
    margin: var(--space-2) 0 0;
    max-width: 68ch;
    color: var(--muted-foreground);
    font-size: 0.875rem;
  }
  .filter select {
    min-height: 2.125rem;
    padding: 0 var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    color: var(--foreground);
    font: inherit;
    font-size: 0.8125rem;
  }
  .scope {
    display: flex;
    gap: var(--space-2);
  }
  /* The register leads and the open record sits beside it — the index stays visible while a record
     is being read, so the author never loses their place in the queue.
     With NO record open the index takes the full width instead of competing with an empty panel:
     reserving half the viewport for a placeholder was what squeezed titles into two lines and drove
     the phase badge into the action button. */
  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-4);
    align-items: start;
  }
  .workspace--open {
    /* The open record gets the larger share: it holds the pipeline, findings, audit trail and the
       PR banner's links, while the register only needs to stay scannable. */
    grid-template-columns: minmax(0, 0.85fr) minmax(30rem, 1.15fr);
  }
  .working-panel {
    min-width: 0;
  }
  .working-panel__loading {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-5);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .workspace--loading {
    opacity: 0.75;
  }
  .error-state {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
  @media (max-width: 1040px) {
    .workspace {
      grid-template-columns: 1fr;
    }
  }
</style>

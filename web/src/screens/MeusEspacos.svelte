<script lang="ts">
  // S3 (D3/GR3): merges the old "Meus espaços" (promote grid) and "Minhas promoções" (history
  // list) into one space-grouped page — a space's own promotion history lives inline with it,
  // instead of two separate nav destinations.
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import EspacoRow from '../lib/components/EspacoRow.svelte';
  import PromotionConfirm from '../lib/components/PromotionConfirm.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
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

  // The user's promotable resources (OBO). In $state so an error is retryable.
  let resourcesP = $state(getResources());

  // History is best-effort — a fetch failure here must never block the primary "start a
  // promotion" flow (the space grid still needs to render). Admins/Stewards may load ALL
  // promotions (LB5's existing scope toggle, carried over from "Minhas promoções").
  let scope = $state<'mine' | 'all'>('mine');
  function loadPromotions(s: 'mine' | 'all'): Promise<PromotionSummary[]> {
    return getPromotions(s).catch(() => []);
  }
  let promotionsP = $state(loadPromotions('mine'));
  function setScope(s: 'mine' | 'all'): void {
    scope = s;
    promotionsP = loadPromotions(s);
  }

  // `promotion` is the single source of truth for the selection — no parallel local state.
  const reload = () => {
    resourcesP = getResources();
    promotionsP = loadPromotions(scope);
    promotion.select(null); // a fresh list invalidates any prior (possibly gone) selection
  };

  // G3: choosing a card only SELECTS the space (select() resets prior verdict state) — it does NOT
  // fire the request. The next step is the confirmation panel below, bound to the chosen space,
  // where the Requester may optionally declare access before actually requesting the promotion
  // (each space still promotes on its OWN branch/PR — see app_logic.space_slug). D7: if the space
  // already has an open Promotion, the backend's existing per-slug idempotency folds this request
  // into that SAME Promotion (a new Review Snapshot) — no special-casing needed here.
  function chooseSpace(resource: PromotableResource): void {
    promotion.select(resource);
  }

  // A space is picked but not yet requested — the grid locks (only "← Escolher outro espaço" in the
  // confirmation panel can change the selection) while that step is on screen.
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
    for (const [rid, list] of byResource) {
      if (seen.has(rid)) continue;
      const first = list[0];
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
      const aOpen = a.promotions.some((p) => !p.terminal);
      const bOpen = b.promotions.some((p) => !p.terminal);
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
    const latest = group.promotions[0];
    return !!latest && statusBucket(latest.current_phase) === statusFilter;
  }

  let expandedIds = $state<Set<string>>(new Set());
  function toggleExpanded(id: string): void {
    const next = new Set(expandedIds);
    next.has(id) ? next.delete(id) : next.add(id);
    expandedIds = next;
  }
</script>

<div class="author-home">
  <header class="hero">
    <div>
      <p class="hero__crumb">Genie / Meus Espaços</p>
      <h1>Do rascunho à produção,<br />sem perder o fio.</h1>
      <p class="hero__copy">
        Escolha um Space, confirme seu público e acompanhe cada promoção no mesmo lugar.
        A edição continua no Dev; a publicação passa pelo Steward.
      </p>
    </div>
  </header>

  {#await Promise.all([resourcesP, promotionsP])}
    <div class="workspace workspace--loading">
      <div class="space-list"><Skeleton height="7rem" /><Skeleton height="7rem" /></div>
      <Skeleton height="34rem" />
    </div>
  {:then [resources, promotions]}
    {@const allGroups = sortGroups(groupBySpace(resources, promotions))}
    {@const shownGroups = allGroups.filter(matchesFilter)}
    {@const selectedGroup = allGroups.find((group) => group.resource.id === promotion.resource?.id)}

    <div class="toolbar">
      <label class="toolbar__search">
        <span aria-hidden="true">⌕</span>
        <span class="visually-hidden">Buscar Space</span>
        <input bind:value={searchQuery} placeholder="Buscar por nome do Space" />
      </label>
      <label class="toolbar__status">
        <span class="visually-hidden">Filtrar por status</span>
        <select bind:value={statusFilter} aria-label="Filtrar por status">
          {#each STATUS_FILTERS as filter (filter.key)}
            <option value={filter.key}>{filter.label}</option>
          {/each}
        </select>
      </label>
      <span class="toolbar__count"><strong>{resources.length}</strong> Spaces disponíveis em Dev</span>
      {#if who?.is_admin}
        <div class="toolbar__scope" role="group" aria-label="Escopo do histórico">
          <Button variant={scope === 'mine' ? 'primary' : 'outline'} onclick={() => setScope('mine')}>Minhas</Button>
          <Button variant={scope === 'all' ? 'primary' : 'outline'} onclick={() => setScope('all')}>Todas (Steward/Admin)</Button>
        </div>
      {/if}
    </div>

    {#if resources.length === 0 && allGroups.length === 0 && !who?.is_admin}
      <div class="empty">
        <p class="empty__title">Nenhum Genie Space encontrado</p>
        <p class="muted text-sm">Crie um no Genie nativo do workspace de dev — depois ele aparece aqui para promoção.</p>
      </div>
    {:else}
      <div class="workspace">
        <section class="space-list" aria-label="Spaces disponíveis">
          {#if shownGroups.length === 0}
            <div class="empty">
              <p class="empty__title">Nenhum Genie Space encontrado</p>
              <p class="muted text-sm">Nenhum espaço corresponde à busca e ao status selecionados.</p>
            </div>
          {:else}
            {#each shownGroups as group (group.resource.id)}
              <EspacoRow
                resource={group.resource}
                promotions={group.promotions}
                expanded={expandedIds.has(group.resource.id)}
                onToggle={() => toggleExpanded(group.resource.id)}
                {onOpenPromotion}
                onPromote={chooseSpace}
                busy={promotion.phase === 'reviewing' && promotion.resource?.id === group.resource.id}
                disabled={promotion.phase === 'reviewing' && promotion.resource?.id !== group.resource.id}
                selected={promotion.resource?.id === group.resource.id}
              />
            {/each}
          {/if}
        </section>

        <section class="working-panel" aria-live="polite">
          {#if confirming}
            {#key promotion.selectionSeq}
              <PromotionConfirm
                {promotion}
                promotions={selectedGroup?.promotions ?? []}
                {onOpenPromotion}
                onCancel={() => promotion.select(null)}
              />
            {/key}
          {:else if promotion.initiatedHere}
            <PromotionReview {promotion} userEmail={who?.email ?? null} {devHost} {prodHost} />
          {:else}
            <div class="working-panel__empty">
              <span aria-hidden="true">↖</span>
              <h2>Escolha um Space</h2>
              <p>O título, o público, as tabelas e o histórico aparecerão aqui sem tirar você do contexto.</p>
            </div>
          {/if}
        </section>
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
  .author-home { display: flex; flex-direction: column; gap: var(--space-5); }
  .hero {
    position: relative;
    padding: var(--space-3) 0 var(--space-4);
    overflow: clip;
  }
  .hero::after {
    content: '';
    position: absolute;
    width: 16rem;
    height: 16rem;
    right: -5rem;
    top: -7rem;
    border-radius: 50%;
    background: radial-gradient(circle, color-mix(in srgb, var(--destructive) 9%, transparent), transparent 68%);
    pointer-events: none;
  }
  .hero__crumb {
    margin: 0 0 var(--space-3);
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  .hero h1 {
    max-width: 48rem;
    font-family: var(--font-display, Georgia, serif);
    font-size: clamp(2.2rem, 5vw, 4rem);
    line-height: 0.95;
    letter-spacing: -0.055em;
  }
  .hero__copy {
    max-width: 48rem;
    margin: var(--space-4) 0 0;
    color: var(--muted-foreground);
    font-size: 0.95rem;
  }
  .toolbar {
    display: grid;
    grid-template-columns: minmax(14rem, 1fr) auto auto;
    align-items: center;
    gap: var(--space-3);
  }
  .toolbar__search {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-width: 0;
    padding: 0.7rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--surface) 88%, transparent);
  }
  .toolbar__search input {
    min-width: 0;
    width: 100%;
    border: 0;
    outline: 0;
    background: transparent;
    font: inherit;
  }
  .toolbar__status select {
    min-height: 2.85rem;
    padding: 0 var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    color: var(--foreground);
    font: inherit;
  }
  .toolbar__count {
    padding-left: var(--space-3);
    border-left: 3px solid var(--destructive);
    color: var(--muted-foreground);
    font-size: 0.72rem;
  }
  .toolbar__count strong {
    display: block;
    color: var(--foreground);
    font-family: var(--font-display, Georgia, serif);
    font-size: 1.5rem;
  }
  .toolbar__scope {
    grid-column: 1 / -1;
    display: flex;
    gap: var(--space-2);
  }
  .workspace {
    display: grid;
    grid-template-columns: minmax(16rem, 0.75fr) minmax(28rem, 1.6fr);
    gap: var(--space-4);
    align-items: start;
  }
  .space-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
  }
  .working-panel {
    min-width: 0;
    border-radius: var(--radius);
  }
  .working-panel__empty {
    min-height: 24rem;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    padding: var(--space-6);
    border: 1px dashed var(--border-strong);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--surface) 75%, transparent);
  }
  .working-panel__empty > span { color: var(--accent-hover); font-size: 1.5rem; }
  .working-panel__empty h2 {
    margin-top: var(--space-2);
    font-family: var(--font-display, Georgia, serif);
  }
  .working-panel__empty p { max-width: 28rem; color: var(--muted-foreground); }
  .workspace--loading { opacity: 0.75; }
  .empty {
    padding: var(--space-5);
    border: 1px dashed var(--border);
    border-radius: var(--radius);
  }
  .empty__title { margin: 0; font-weight: 700; }
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
    .workspace { grid-template-columns: 1fr; }
    .space-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .space-list :global(.espaco-row__history) { grid-column: 1 / -1; }
  }
  @media (max-width: 720px) {
    .hero h1 { font-size: clamp(2rem, 12vw, 3rem); }
    .toolbar { grid-template-columns: minmax(0, 1fr) auto; }
    .toolbar__count { display: none; }
    .space-list { grid-template-columns: 1fr; }
    .workspace { min-width: 0; }
  }
</style>

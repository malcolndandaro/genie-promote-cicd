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

  function matchesFilter(group: EspacoGroup): boolean {
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

<div class="stack">
  <section>
    <header class="screen-head">
      <h2 class="screen-head__title">Meus espaços</h2>
      <p class="muted text-sm">Escolha um espaço, veja seu histórico e solicite a promoção governada para produção.</p>
    </header>

    {#await Promise.all([resourcesP, promotionsP])}
      <div class="space-list">
        <Skeleton height="6rem" />
        <Skeleton height="6rem" />
        <Skeleton height="6rem" />
      </div>
    {:then [resources, promotions]}
      {@const allGroups = sortGroups(groupBySpace(resources, promotions))}
      {@const shownGroups = allGroups.filter(matchesFilter)}
      {#if resources.length === 0 && allGroups.length === 0 && !who?.is_admin}
        <!-- Truly nothing to show and no reason to offer the toolbar (no personal spaces, no
             personal history, not an admin who could switch scope to see others'). -->
        <div class="empty">
          <p class="empty__title">Nenhum Genie Space encontrado</p>
          <p class="muted text-sm">
            Crie um no Genie nativo do workspace de dev — depois ele aparece aqui para promoção.
          </p>
        </div>
      {:else}
        <div class="toolbar">
          <div class="toolbar__filters" role="group" aria-label="Filtrar por status">
            {#each STATUS_FILTERS as f (f.key)}
              <Button
                variant={statusFilter === f.key ? 'primary' : 'outline'}
                onclick={() => (statusFilter = f.key)}
              >
                {f.label}
              </Button>
            {/each}
          </div>
          {#if who?.is_admin}
            <div class="toolbar__scope" role="group" aria-label="Escopo do histórico">
              <Button variant={scope === 'mine' ? 'primary' : 'outline'} onclick={() => setScope('mine')}>Minhas</Button>
              <Button variant={scope === 'all' ? 'primary' : 'outline'} onclick={() => setScope('all')}>Todas (Steward/Admin)</Button>
            </div>
          {/if}
        </div>

        {#if shownGroups.length === 0}
          <div class="empty">
            <p class="empty__title">Nenhum Genie Space encontrado</p>
            <p class="muted text-sm">
              {#if allGroups.length > 0}
                Nenhum espaço corresponde ao filtro selecionado.
              {:else}
                Crie um no Genie nativo do workspace de dev — depois ele aparece aqui para promoção.
              {/if}
            </p>
          </div>
        {:else}
          <div class="space-list">
            {#each shownGroups as group (group.resource.id)}
              <EspacoRow
                resource={group.resource}
                promotions={group.promotions}
                expanded={expandedIds.has(group.resource.id)}
                onToggle={() => toggleExpanded(group.resource.id)}
                {onOpenPromotion}
                onPromote={chooseSpace}
                busy={promotion.phase === 'reviewing' && promotion.resource?.id === group.resource.id}
                disabled={promotion.phase === 'reviewing' ? promotion.resource?.id !== group.resource.id : confirming}
                selected={confirming && promotion.resource?.id === group.resource.id}
              />
            {/each}
          </div>
        {/if}
      {/if}
    {:catch err}
      <div class="error-state" role="alert">
        <span class="error">
          {#if isAuthError(err)}
            Sessão expirada — recarregue a página para reautenticar.
          {:else}
            Não foi possível listar os espaços: {err instanceof Error ? err.message : String(err)}
          {/if}
        </span>
        {#if isAuthError(err)}
          <Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
        {:else}
          <Button variant="outline" onclick={reload}>Tentar novamente</Button>
        {/if}
      </div>
    {/await}
  </section>

  <!-- G3: the confirmation step — bound to the CHOSEN space (title visible), optional access
       declaration, then "Confirmar promoção". Shown only until the promotion is actually requested.
       Keyed by `selectionSeq` (NOT `resource?.id` — found live, PR #25: re-selecting the SAME space
       must ALSO remount the panel, or its declaration forms keep their prior rows/title and the
       stale audience/table-mapping/title silently rides the next request) so EVERY select(), same-
       space included, remounts PromotionMappingForm/AudienceSpecForm fresh. -->
  {#if confirming}
    {#key promotion.selectionSeq}
      <PromotionConfirm {promotion} onCancel={() => promotion.select(null)} />
    {/key}
  {/if}

  <!-- The active promotion's review/pipeline/approval (in-place below the grid) — shown ONLY when the
       user actively requested a promotion on this screen this session (`initiatedHere`), never
       auto-pinned to the last promotion recovered on page load. To revisit a past promotion's
       pipeline, expand the space (its history) and open the attempt (→ #/promocoes/:id). -->
  {#if promotion.initiatedHere}
    <PromotionReview {promotion} userEmail={who?.email ?? null} {devHost} {prodHost} />
  {/if}
</div>

<style>
  .screen-head {
    margin-bottom: var(--space-4);
  }
  .screen-head__title {
    font-size: 1.2rem;
  }
  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    flex-wrap: wrap;
    margin-bottom: var(--space-4);
  }
  .toolbar__filters,
  .toolbar__scope {
    display: flex;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .space-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4) 0;
  }
  .empty__title {
    margin: 0;
    font-weight: 600;
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
</style>

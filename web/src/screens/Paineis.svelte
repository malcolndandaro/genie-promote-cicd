<script lang="ts">
  // The DEDICATED promotion surface for AI/BI dashboards, built as a DOSSIER INDEX (see the
  // direction contract in App.svelte).
  //
  // Genie Spaces keep "Meus espaços"; painéis have their own destination because the two resources
  // are authored, described and reviewed differently. A single shared LIST forced Genie vocabulary
  // ("espaço", "benchmarks", "CAN_RUN") onto a painel and left no room for what a painel actually is:
  // datasets, widgets, pages, and the area it is filed under.
  //
  // What this redesign changes: the two pages now share one register GRAMMAR (DossierIndex /
  // DossierRow / DossierToolbar / DossierEmpty) so an author learns the flow once — while every WORD
  // and every piece of evidence stays dashboard-native. The structural summary (datasets / widgets /
  // pages) is read from the real definition in the confirm step, where the definition is actually
  // fetched; the index never invents counts it has not loaded.
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import DossierIndex from '../lib/components/DossierIndex.svelte';
  import DossierRow from '../lib/components/DossierRow.svelte';
  import DossierToolbar from '../lib/components/DossierToolbar.svelte';
  import DossierEmpty from '../lib/components/DossierEmpty.svelte';
  import PainelConfirm from '../lib/components/PainelConfirm.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import PromotionList from '../lib/components/PromotionList.svelte';
  import { getResources, getPromotions, isAuthError, type PromotionSummary } from '../lib/api';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { PromotableResource, Whoami } from '../lib/types';

  interface Props {
    promotion: Promotion;
    who: Whoami | null;
    devHost?: string | null;
    prodHost?: string | null;
    onOpenPromotion: (summary: PromotionSummary) => void;
  }
  let { promotion, who, devHost = null, prodHost = null, onOpenPromotion }: Props = $props();

  // Only dashboards — this page never shows a Genie Space.
  let resourcesP = $state(loadDashboards());
  function loadDashboards(): Promise<PromotableResource[]> {
    return getResources().then((all) => all.filter((r) => r.kind === 'dashboard'));
  }

  // History is best-effort: a failure here must not block the primary "promote a painel" flow.
  let promotionsP = $state(getPromotions('mine').catch(() => []));

  const reload = () => {
    resourcesP = loadDashboards();
    promotionsP = getPromotions('mine').catch(() => []);
    promotion.select(null);
  };

  function choose(resource: PromotableResource): void {
    promotion.select(resource);
  }

  /** A painel is picked but not yet requested — the register locks while the confirm step is up. */
  let confirming = $derived(
    !!promotion.resource && promotion.resource.kind === 'dashboard' && promotion.phase === 'idle',
  );
  /** Show the inline pipeline only for a promotion started HERE this session. */
  let reviewing = $derived(
    !!promotion.resource && promotion.resource.kind === 'dashboard' && promotion.initiatedHere,
  );

  let searchQuery = $state('');
  function matches(resource: PromotableResource): boolean {
    const q = searchQuery.trim().toLocaleLowerCase('pt-BR');
    return !q || resource.title.toLocaleLowerCase('pt-BR').includes(q);
  }

  /** This painel's own promotion history (newest first). */
  function historyFor(all: PromotionSummary[], resourceId: string): PromotionSummary[] {
    return all
      .filter((p) => p.resource_id === resourceId)
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  const TERMINAL_PHASES = new Set(['deployed', 'deploy_failed', 'revision_mismatch', 'closed']);
  function activePhaseFor(resourceId: string): string | null {
    if (!promotion.initiatedHere || promotion.resource?.id !== resourceId) return null;
    return (
      promotion.liveStatus?.phase ??
      (promotion.phase === 'reviewing' ? 'checks_running' : promotion.pr ? 'open' : null)
    );
  }

  let expandedIds = $state<Set<string>>(new Set());
  function toggleExpanded(id: string): void {
    const next = new Set(expandedIds);
    next.has(id) ? next.delete(id) : next.add(id);
    expandedIds = next;
  }
</script>

<section class="paineis" aria-label="Painéis AI/BI">
  <header class="page-head">
    <h1>Promover um painel AI/BI</h1>
    <p>
      Escolha o painel no workspace de dev. O app confere a estrutura (datasets, widgets, páginas),
      valida o SQL contra produção e abre um rascunho de PR para o Responsável Técnico revisar.
    </p>
  </header>

  {#await resourcesP}
    <div class="workspace workspace--loading">
      <div class="stack"><Skeleton height="4rem" /><Skeleton height="4rem" /><Skeleton height="4rem" /></div>
      <Skeleton height="24rem" />
    </div>
  {:then resources}
    {@const visible = resources.filter(matches)}

    <DossierToolbar
      bind:query={searchQuery}
      searchLabel="Buscar por nome do painel"
      searchName="Buscar painel"
      countLabel={`${resources.length} ${resources.length === 1 ? 'painel disponível' : 'painéis disponíveis'} em Dev`}
    />

    {#if resources.length === 0}
      <DossierIndex label="Painéis disponíveis" identHeader="Painel AI/BI">
        <DossierEmpty
          icon="chart"
          title="Nenhum painel AI/BI encontrado"
          hint="Crie um painel AI/BI no workspace de dev — depois ele aparece aqui para promoção. Painéis na lixeira não são promovíveis."
        />
      </DossierIndex>
    {:else}
      {@const recordOpen = confirming || reviewing}
      <div class="workspace" class:workspace--open={recordOpen}>
        <DossierIndex label="Painéis disponíveis" identHeader="Painel AI/BI">
          {#if visible.length === 0}
            <DossierEmpty
              icon="chart"
              title="Nenhum painel corresponde à busca"
              hint="Ajuste o termo buscado para encontrar o painel."
            />
          {:else}
            {#await promotionsP then promotions}
              {#each visible as resource (resource.id)}
                {@const rows = historyFor(promotions, resource.id)}
                {@const activePhase = activePhaseFor(resource.id)}
                {@const latest = rows[0] ?? null}
                <DossierRow
                  {resource}
                  icon="chart"
                  kindLabel="Painel AI/BI"
                  promotions={rows}
                  expanded={expandedIds.has(resource.id)}
                  onToggle={() => toggleExpanded(resource.id)}
                  onPromote={choose}
                  onOpenLatest={latest ? () => onOpenPromotion(latest) : undefined}
                  busy={promotion.phase === 'reviewing' && promotion.resource?.id === resource.id}
                  disabled={confirming && promotion.resource?.id !== resource.id}
                  selected={promotion.resource?.id === resource.id}
                  {activePhase}
                  activeRequester={activePhase ? promotion.requesterEmail : null}
                  activeTerminal={activePhase ? TERMINAL_PHASES.has(activePhase) : false}
                  activePhaseLoading={promotion.initiatedHere
                    && promotion.resource?.id === resource.id
                    && promotion.waitingForLiveStatus}
                >
                  {#snippet facts()}
                    <!-- Dashboard-native, and only what this endpoint actually returned: the
                         structural summary needs the full definition, which the confirm step
                         fetches. Promising counts here would mean N definition reads per index. -->
                    <span>Estrutura conferida ao preparar a promoção</span>
                  {/snippet}
                  {#snippet history()}
                    <PromotionList
                      promotions={rows}
                      onOpen={onOpenPromotion}
                      emptyTitle="Nunca promovido"
                      emptyHint=""
                    />
                  {/snippet}
                </DossierRow>
              {/each}
            {/await}
          {/if}
        </DossierIndex>

        <!-- Rendered only when a record is open — same rule as the Genie register. -->
        {#if recordOpen}
          <section class="working-panel" aria-label="Promoção do painel" aria-live="polite">
            {#if confirming}
              {#key promotion.selectionSeq}
                <PainelConfirm {promotion} onCancel={() => promotion.select(null)} />
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
        {#if isAuthError(err)}
          Sessão expirada — recarregue a página para reautenticar.
        {:else}
          Não foi possível listar os painéis: {err instanceof Error ? err.message : String(err)}
        {/if}
      </span>
      <Button variant="outline" onclick={reload}>Tentar novamente</Button>
    </div>
  {/await}
</section>

<style>
  .paineis {
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
  /* Same proportions as the Genie register — the shared grammar is what makes the two pages read as
     one product rather than two tools. */
  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-4);
    align-items: start;
  }
  .workspace--open {
    grid-template-columns: minmax(0, 1.1fr) minmax(26rem, 1fr);
  }
  .working-panel {
    min-width: 0;
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

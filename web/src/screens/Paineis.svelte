<script lang="ts">
  // The DEDICATED page for promoting AI/BI dashboards.
  //
  // Genie Spaces keep "Meus espaços"; painéis get their own destination because the two resources are
  // authored, described and reviewed differently. A single shared list forced Genie vocabulary
  // ("espaço", "benchmarks", "CAN_RUN") onto a painel and left no room for what a painel actually is:
  // datasets, widgets, pages, and the area it is filed under.
  //
  // What is dashboard-native here and NOT on the Genie page:
  //   - a structural summary (datasets / widgets / pages) read from the real definition, so the author
  //     sees WHAT they are promoting before promoting it;
  //   - the business area + the exact repository path the promotion will commit to;
  //   - dashboard quality language (checagens estruturais), never eval-run/benchmarks.
  import Button from '../lib/components/Button.svelte';
  import Card from '../lib/components/Card.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import PainelCard from '../lib/components/PainelCard.svelte';
  import PainelConfirm from '../lib/components/PainelConfirm.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import PromotionList from '../lib/components/PromotionList.svelte';
  import { getResources, getPromotions, isAuthError, type PromotionSummary } from '../lib/api';
  import { phaseChip } from '../lib/status';
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

  /** A painel is picked but not yet requested — the list locks while the confirm step is up. */
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

  function activePhaseFor(resourceId: string): string | null {
    if (!promotion.initiatedHere || promotion.resource?.id !== resourceId) return null;
    return (
      promotion.liveStatus?.phase ??
      (promotion.phase === 'reviewing' ? 'checks_running' : promotion.pr ? 'open' : null)
    );
  }
</script>

<section class="paineis" aria-label="Painéis AI/BI">
  <header class="process-head">
    <h1>Promover um painel AI/BI</h1>
    <p>
      Escolha o painel no workspace de dev. O app confere a estrutura (datasets, widgets, páginas),
      valida o SQL contra produção e abre um rascunho de PR para o Responsável Técnico revisar.
    </p>
  </header>

  {#await resourcesP}
    <div class="skeletons"><Skeleton /><Skeleton /><Skeleton /></div>
  {:then resources}
    {@const visible = resources.filter(matches)}
    <div class="toolbar">
      <label class="toolbar__search">
        <span class="visually-hidden">Buscar painel</span>
        <input bind:value={searchQuery} placeholder="Buscar por nome do painel" />
      </label>
      <span class="toolbar__count">
        <strong>{resources.length}</strong>
        {resources.length === 1 ? 'painel disponível' : 'painéis disponíveis'} em Dev
      </span>
    </div>

    {#if resources.length === 0}
      <Card>
        <div class="empty">
          <p class="empty__title">Nenhum painel AI/BI encontrado</p>
          <p class="muted text-sm">
            Crie um painel AI/BI no workspace de dev — depois ele aparece aqui para promoção. Painéis
            na lixeira não são promovíveis.
          </p>
        </div>
      </Card>
    {:else}
      <div class="workspace">
        <div class="painel-list">
          {#if visible.length === 0}
            <Card>
              <div class="empty">
                <p class="empty__title">Nenhum painel corresponde à busca</p>
              </div>
            </Card>
          {:else}
            {#await promotionsP then promotions}
              {#each visible as resource (resource.id)}
                {@const history = historyFor(promotions, resource.id)}
                <div class="painel-entry">
                  <PainelCard
                    {resource}
                    onPromote={choose}
                    busy={promotion.phase === 'reviewing' && promotion.resource?.id === resource.id}
                    disabled={confirming && promotion.resource?.id !== resource.id}
                    selected={promotion.resource?.id === resource.id}
                  />
                  {#if activePhaseFor(resource.id)}
                    {@const chip = phaseChip(activePhaseFor(resource.id))}
                    <Badge tone={chip.tone}>{chip.label}</Badge>
                  {/if}
                  {#if history.length > 0}
                    <details class="painel-entry__history">
                      <summary>
                        {history.length}
                        {history.length === 1 ? 'promoção' : 'promoções'} deste painel
                      </summary>
                      <PromotionList
                        promotions={history}
                        onOpen={onOpenPromotion}
                        emptyTitle="Nunca promovido"
                        emptyHint=""
                      />
                    </details>
                  {/if}
                </div>
              {/each}
            {/await}
          {/if}
        </div>

        <section class="working-panel" aria-label="Promoção do painel">
          {#if confirming}
            {#key promotion.selectionSeq}
              <PainelConfirm {promotion} onCancel={() => promotion.select(null)} />
            {/key}
          {:else if reviewing}
            <PromotionReview {promotion} userEmail={who?.email ?? null} {devHost} {prodHost} />
          {:else}
            <div class="working-panel__empty">
              <span aria-hidden="true">▤</span>
              <h2>Escolha um painel</h2>
              <p>
                A estrutura, a área de negócio, o público e o histórico aparecem aqui sem tirar você do
                contexto.
              </p>
            </div>
          {/if}
        </section>
      </div>
    {/if}
  {:catch err}
    <Card>
      <div class="empty">
        <p class="empty__title">
          {#if isAuthError(err)}
            Sessão expirada — recarregue a página para reautenticar.
          {:else}
            Não foi possível listar os painéis: {err instanceof Error ? err.message : String(err)}
          {/if}
        </p>
        <Button variant="outline" onclick={reload}>Tentar novamente</Button>
      </div>
    </Card>
  {/await}
</section>

<style>
  .paineis {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .process-head h1 {
    margin: 0 0 var(--space-1);
    font-size: 1.35rem;
  }
  .process-head p {
    margin: 0;
    color: var(--muted-foreground);
    max-width: 68ch;
  }
  .skeletons {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .toolbar {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .toolbar__search {
    flex: 1;
    min-width: 12rem;
  }
  .toolbar__search input {
    width: 100%;
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
    background: var(--surface);
    color: inherit;
  }
  .toolbar__search input:focus {
    outline: 2px solid var(--ring);
    outline-offset: 1px;
  }
  .toolbar__count {
    color: var(--muted-foreground);
    font-size: 0.85rem;
  }
  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 22rem) minmax(0, 1fr);
    gap: var(--space-4);
    align-items: start;
  }
  .painel-list {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .painel-entry {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .painel-entry__history summary {
    cursor: pointer;
    font-size: 0.8rem;
    color: var(--muted-foreground);
    padding: 0.2rem 0;
  }
  .working-panel {
    min-width: 0;
  }
  .working-panel__empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-6) var(--space-4);
    border: 1px dashed var(--border);
    border-radius: var(--radius);
    text-align: center;
    color: var(--muted-foreground);
  }
  .working-panel__empty span {
    font-size: 1.6rem;
  }
  .working-panel__empty h2 {
    margin: 0;
    font-size: 1.05rem;
    color: var(--foreground);
  }
  .working-panel__empty p {
    margin: 0;
    max-width: 42ch;
    font-size: 0.9rem;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
    padding: var(--space-2);
  }
  .empty__title {
    margin: 0;
    font-weight: 600;
  }
  @media (max-width: 900px) {
    .workspace {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>

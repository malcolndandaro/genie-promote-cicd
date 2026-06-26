<script lang="ts">
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import SpaceCard from '../lib/components/SpaceCard.svelte';
  import PromotionList from '../lib/components/PromotionList.svelte';
  import FlowSteps from '../lib/components/FlowSteps.svelte';
  import { getResources, getPromotions, isAuthError, type PromotionSummary } from '../lib/api';
  import { formatHash } from '../lib/route';
  import type { PromotableResource } from '../lib/types';

  interface Props {
    /** Start a promotion for a space (the parent selects it + enters the review flow). */
    onPromote: (resource: PromotableResource) => void;
    /** Open a promotion (deep-link to its detail). */
    onOpenPromotion: (summary: PromotionSummary) => void;
    /** Go to the authoring guide. */
    onGoToNew: () => void;
    /** A promotion is already in flight — disable the cards to avoid a concurrent request. */
    promoting?: boolean;
  }
  let { onPromote, onOpenPromotion, onGoToNew, promoting = false }: Props = $props();

  // Compose the existing OBO reads — no aggregate endpoint needed. Retryable via $state.
  // NB this duplicates the `getPromotions('mine')` that App's recover-on-load also issues; both are
  // idempotent reads. A future `/api/home` aggregate would collapse them — deliberately out of scope.
  let spacesP = $state(getResources());
  let promosP = $state(getPromotions('mine'));
  const reloadSpaces = () => (spacesP = getResources());
  const reloadPromos = () => (promosP = getPromotions('mine'));

  const RECENT_LIMIT = 4;
</script>

<div class="stack home">
  <!-- What this app does + the simplified governed flow. -->
  <Card>
    <div class="hero">
      <h2 class="hero__title">Promova seus Genie Spaces para produção, com governança</h2>
      <p class="hero__lead muted">
        Autore no ambiente de desenvolvimento, deixe a revisão automatizada validar a qualidade e a
        segurança, e promova para produção por um fluxo aprovado — tudo em um só lugar.
      </p>
      <FlowSteps />
    </div>
  </Card>

  <!-- Meus espaços (cards) -->
  <section>
    <div class="section-head">
      <h3 class="section-head__title">Meus espaços</h3>
      <a class="section-head__link" href={formatHash({ id: 'espacos' })}>Ver todos →</a>
    </div>
    {#await spacesP}
      <div class="space-grid">
        <Skeleton height="9rem" />
        <Skeleton height="9rem" />
        <Skeleton height="9rem" />
      </div>
    {:then resources}
      {#if resources.length === 0}
        <Card>
          <div class="empty">
            <p class="empty__title">Nenhum Genie Space encontrado</p>
            <p class="muted text-sm">Crie um para começar — a autoria acontece no Genie nativo.</p>
            <Button variant="outline" onclick={onGoToNew}>＋ Novo Genie Space</Button>
          </div>
        </Card>
      {:else}
        <div class="space-grid">
          {#each resources.slice(0, 6) as r (r.id)}
            <SpaceCard resource={r} {onPromote} disabled={promoting} />
          {/each}
        </div>
      {/if}
    {:catch err}
      <Card>
        <div class="error-state" role="alert">
          <span class="error">
            {#if isAuthError(err)}
              Sessão expirada — recarregue para reautenticar.
            {:else}
              Não foi possível listar os espaços.
            {/if}
          </span>
          {#if isAuthError(err)}
            <Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
          {:else}
            <Button variant="outline" onclick={reloadSpaces}>Tentar novamente</Button>
          {/if}
        </div>
      </Card>
    {/await}
  </section>

  <!-- Promoções recentes -->
  <section>
    <div class="section-head">
      <h3 class="section-head__title">Promoções recentes</h3>
      <a class="section-head__link" href={formatHash({ id: 'promocoes' })}>Ver todas →</a>
    </div>
    <Card>
      {#await promosP}
        <div class="stack">
          <Skeleton height="3.2rem" />
          <Skeleton height="3.2rem" width="80%" />
        </div>
      {:then promotions}
        <PromotionList
          {promotions}
          onOpen={onOpenPromotion}
          limit={RECENT_LIMIT}
          emptyTitle="Nenhuma promoção ainda"
          emptyHint="Escolha um espaço acima e clique em “Solicitar promoção” para começar."
        />
      {:catch err}
        <div class="error-state" role="alert">
          <span class="error">
            {#if isAuthError(err)}
              Sessão expirada — recarregue para reautenticar.
            {:else}
              Não foi possível carregar as promoções.
            {/if}
          </span>
          {#if isAuthError(err)}
            <Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
          {:else}
            <Button variant="outline" onclick={reloadPromos}>Tentar novamente</Button>
          {/if}
        </div>
      {/await}
    </Card>
  </section>
</div>

<style>
  .hero {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .hero__title {
    font-size: 1.4rem;
    line-height: 1.25;
  }
  .hero__lead {
    margin: 0;
    max-width: 60ch;
    font-size: 0.95rem;
  }
  .section-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-3);
  }
  .section-head__title {
    font-size: 1.1rem;
  }
  .section-head__link {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--accent-hover);
    white-space: nowrap;
  }
  .space-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: var(--space-4);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
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

<script lang="ts">
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import SpaceCard from '../lib/components/SpaceCard.svelte';
  import AccessSpecForm from '../lib/components/AccessSpecForm.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import { getResources, isAuthError } from '../lib/api';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { PromotableResource } from '../lib/types';

  interface Props {
    promotion: Promotion;
    userEmail: string | null;
    onGoToNew?: () => void;
  }
  let { promotion, userEmail, onGoToNew }: Props = $props();

  // The user's promotable resources (OBO). In $state so an error is retryable.
  let resourcesP = $state(getResources());

  // `promotion` is the single source of truth for the selection — no parallel local state.
  const reload = () => {
    resourcesP = getResources();
    promotion.select(null); // a fresh list invalidates any prior (possibly gone) selection
  };

  // Per-space promotion: selecting a card requests its promotion directly (each space promotes on
  // its OWN branch/PR — see app_logic.space_slug). select() resets prior verdict state. The review
  // renders in-place below via <PromotionReview>.
  function promote(resource: PromotableResource): void {
    promotion.select(resource);
    promotion.requestPromotion();
  }
</script>

<div class="stack">
  <section>
    <header class="screen-head">
      <h2 class="screen-head__title">Meus espaços</h2>
      <p class="muted text-sm">Escolha um espaço e solicite a promoção governada para produção.</p>
    </header>

    <!-- F2: optionally declare who may use the promoted Space + read its data. Rides the next
         "Solicitar promoção"; shown in the Review for the Steward to approve; applied by the
         governed pipeline (never app-direct). -->
    <div class="access-slot">
      <AccessSpecForm {promotion} />
    </div>

    {#await resourcesP}
      <div class="space-grid">
        <Skeleton height="9rem" />
        <Skeleton height="9rem" />
        <Skeleton height="9rem" />
      </div>
    {:then resources}
      {#if resources.length === 0}
        <div class="empty">
          <p class="empty__title">Nenhum Genie Space encontrado</p>
          <p class="muted text-sm">
            Crie um para começar a promover — a autoria rica acontece no Genie nativo.
          </p>
          {#if onGoToNew}
            <Button variant="outline" onclick={onGoToNew}>＋ Novo Genie Space</Button>
          {/if}
        </div>
      {:else}
        <div class="space-grid">
          {#each resources as r (r.id)}
            <SpaceCard
              resource={r}
              busy={promotion.phase === 'reviewing' && promotion.resource?.id === r.id}
              disabled={promotion.phase === 'reviewing' && promotion.resource?.id !== r.id}
              onPromote={promote}
            />
          {/each}
        </div>
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

  <!-- The active promotion's review/pipeline/approval (in-place below the grid). -->
  <PromotionReview {promotion} {userEmail} />
</div>

<style>
  .screen-head {
    margin-bottom: var(--space-4);
  }
  .access-slot {
    margin-bottom: var(--space-4);
  }
  .screen-head__title {
    font-size: 1.2rem;
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

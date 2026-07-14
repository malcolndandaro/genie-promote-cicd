<script lang="ts">
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import SpaceCard from '../lib/components/SpaceCard.svelte';
  import PromotionConfirm from '../lib/components/PromotionConfirm.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import { getResources, isAuthError } from '../lib/api';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { PromotableResource } from '../lib/types';

  interface Props {
    promotion: Promotion;
    userEmail: string | null;
    /** The dev workspace host (G5) — threaded down to PromotionReview's RehydrateAction. */
    devHost?: string | null;
    /** W3: the prod workspace host — threaded down to PromotionReview's "Abrir Genie em produção". */
    prodHost?: string | null;
  }
  let { promotion, userEmail, devHost = null, prodHost = null }: Props = $props();

  // The user's promotable resources (OBO). In $state so an error is retryable.
  let resourcesP = $state(getResources());

  // `promotion` is the single source of truth for the selection — no parallel local state.
  const reload = () => {
    resourcesP = getResources();
    promotion.select(null); // a fresh list invalidates any prior (possibly gone) selection
  };

  // G3: choosing a card only SELECTS the space (select() resets prior verdict state) — it does NOT
  // fire the request. The next step is the confirmation panel below, bound to the chosen space,
  // where the Requester may optionally declare access before actually requesting the promotion
  // (each space still promotes on its OWN branch/PR — see app_logic.space_slug).
  function chooseSpace(resource: PromotableResource): void {
    promotion.select(resource);
  }

  // A space is picked but not yet requested — the grid locks (only "← Escolher outro espaço" in the
  // confirmation panel can change the selection) while that step is on screen.
  const confirming = $derived(!!promotion.resource && promotion.phase === 'idle');
</script>

<div class="stack">
  <section>
    <header class="screen-head">
      <h2 class="screen-head__title">Meus espaços</h2>
      <p class="muted text-sm">Escolha um espaço e solicite a promoção governada para produção.</p>
    </header>

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
            Crie um no Genie nativo do workspace de dev — depois ele aparece aqui para promoção.
          </p>
        </div>
      {:else}
        <div class="space-grid">
          {#each resources as r (r.id)}
            <SpaceCard
              resource={r}
              busy={promotion.phase === 'reviewing' && promotion.resource?.id === r.id}
              disabled={promotion.phase === 'reviewing' ? promotion.resource?.id !== r.id : confirming}
              selected={confirming && promotion.resource?.id === r.id}
              onPromote={chooseSpace}
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

  <!-- G3: the confirmation step — bound to the CHOSEN space (title visible), optional access
       declaration, then "Confirmar promoção". Shown only until the promotion is actually requested.
       Keyed by `selectionSeq` (NOT `resource?.id` — found live, PR #25: re-selecting the SAME space
       must ALSO remount the panel, or its declaration forms keep their prior rows/title and the
       stale AccessSpec/table-mapping/title silently rides the next request) so EVERY select(), same-
       space included, remounts PromotionMappingForm/AccessSpecForm fresh. -->
  {#if confirming}
    {#key promotion.selectionSeq}
      <PromotionConfirm {promotion} onCancel={() => promotion.select(null)} />
    {/key}
  {/if}

  <!-- The active promotion's review/pipeline/approval (in-place below the grid). -->
  <PromotionReview {promotion} {userEmail} {devHost} {prodHost} />
</div>

<style>
  .screen-head {
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

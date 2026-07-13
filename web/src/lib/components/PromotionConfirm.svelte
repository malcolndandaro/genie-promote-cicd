<script lang="ts">
  // G3: the confirmation step between "choose a space" and "request the promotion" — replaces the
  // old page-level "Declarar Acesso (opcional)" block. Renders ONLY once a space is selected and
  // BEFORE any review runs (promotion.resource set, phase still 'idle'), clearly bound to that space
  // (title visible here). The Requester declares the prod Space name + table de-para (G7) and may
  // optionally declare access (F2), then confirms — all of it rides `requestPromotion()`'s payload
  // (pendingProdTitle/pendingTableMapping/pendingAccessSpec, same capture-before-confirm pattern).
  import Card from './Card.svelte';
  import Button from './Button.svelte';
  import AccessSpecForm from './AccessSpecForm.svelte';
  import PromotionMappingForm from './PromotionMappingForm.svelte';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    /** Back out of the confirmation step — clears the selection so the space grid is pickable again. */
    onCancel: () => void;
  }
  let { promotion, onCancel }: Props = $props();

  const resource = $derived(promotion.resource);
</script>

{#if resource}
  <Card>
    <div class="confirm">
      <header class="confirm__head">
        <p class="confirm__step muted text-sm">Espaço selecionado</p>
        <h3 class="confirm__title">{resource.title}</h3>
      </header>

      <PromotionMappingForm {promotion} />
      <AccessSpecForm {promotion} />

      <div class="confirm__actions">
        <Button variant="outline" onclick={onCancel}>← Escolher outro espaço</Button>
        <Button onclick={() => promotion.requestPromotion()} loading={promotion.phase === 'reviewing'}>
          {promotion.phase === 'reviewing' ? 'Solicitando…' : 'Confirmar promoção'}
        </Button>
      </div>
    </div>
  </Card>
{/if}

<style>
  .confirm {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .confirm__step {
    margin: 0 0 0.15rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 0.72rem;
    font-weight: 700;
  }
  .confirm__title {
    margin: 0;
    font-size: 1.15rem;
  }
  .confirm__actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
</style>

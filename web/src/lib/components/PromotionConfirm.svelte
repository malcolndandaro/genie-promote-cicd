<script lang="ts">
  // G3: the confirmation step between "choose a space" and "request the promotion" — replaces the
  // old page-level "Declarar Acesso (opcional)" block. Renders ONLY once a space is selected and
  // BEFORE any review runs (promotion.resource set, phase still 'idle'), clearly bound to that space
  // (title visible here). The Requester declares the prod Space name + table de-para (G7) and may
  // optionally declare access (F2), then confirms — all of it rides `requestPromotion()`'s payload
  // (pendingProdTitle/pendingTableMapping/pendingAudienceSpec, same capture-before-confirm pattern).
  import Card from './Card.svelte';
  import Button from './Button.svelte';
  import AudienceSpecForm from './AudienceSpecForm.svelte';
  import PromotionMappingForm from './PromotionMappingForm.svelte';
  import PromotionList from './PromotionList.svelte';
  import type { PromotionSummary } from '../api';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    /** Back out of the confirmation step — clears the selection so the space grid is pickable again. */
    onCancel: () => void;
    promotions?: PromotionSummary[];
    onOpenPromotion?: (promotion: PromotionSummary) => void;
  }
  let { promotion, onCancel, promotions = [], onOpenPromotion = () => {} }: Props = $props();

  const resource = $derived(promotion.resource);
  const deployed = $derived(promotions.find((item) => item.current_phase === 'deployed') ?? null);
  const inFlight = $derived(promotions.find((item) => !item.terminal) ?? null);
</script>

{#if resource}
  <Card>
    <div class="confirm">
      <header class="confirm__head">
        <p class="confirm__step">Preparar promoção</p>
        <h3 class="confirm__title">{resource.title}</h3>
        <p class="confirm__sub muted text-sm">
          Confirme como este Space será publicado. A edição continua no Dev e a aprovação passa pelo Steward.
        </p>
      </header>

      <section class="confirm__section">
        <div class="confirm__section-title">
          <h4>1. Como aparecerá em produção</h4><span>editável</span>
        </div>
        <PromotionMappingForm {promotion} />
      </section>

      <section class="confirm__section">
        <AudienceSpecForm {promotion} />
      </section>

      <section class="confirm__section confirm__history" aria-label="Histórico deste Space">
        <div class="confirm__section-title">
          <h4>Histórico deste Space</h4><span>últimas tentativas</span>
        </div>
        <PromotionList
          {promotions}
          onOpen={onOpenPromotion}
          limit={3}
          emptyTitle="Ainda não promovido"
          emptyHint="A primeira solicitação aparecerá aqui."
        />
      </section>

      {#if inFlight}
        <p class="confirm__reuse" role="note">
          Já existe uma solicitação em andamento. A revisão atualizará a mesma promoção e o mesmo Change Request.
        </p>
      {/if}

      <div class="confirm__actions">
        <Button variant="outline" onclick={onCancel}>← Escolher outro espaço</Button>
        <Button
          onclick={() => promotion.requestPromotion()}
          loading={promotion.phase === 'reviewing'}
          disabled={!promotion.pendingAudienceSpec}
          ariaDescribedby="audience-required"
          ariaLabel="Confirmar promoção — Revisar e solicitar promoção"
        >
          {promotion.phase === 'reviewing' ? 'Preparando revisão…' : 'Revisar e solicitar promoção →'}
        </Button>
        <Button
          variant="outline"
          disabled={!deployed}
          onclick={() => deployed && onOpenPromotion(deployed)}
          ariaLabel="Exportar versão Prod para Dev"
        >Exportar versão Prod → Dev</Button>
      </div>
      {#if !deployed}
        <p class="confirm__export-hint muted text-xs">
          A exportação ficará disponível depois da primeira implantação em produção.
        </p>
      {/if}
      {#if !promotion.pendingAudienceSpec}
        <p id="audience-required" class="confirm__required" role="status">
          Selecione ao menos uma pessoa ou grupo para continuar.
        </p>
      {/if}
    </div>
  </Card>
{/if}

<style>
  .confirm {
    display: flex;
    flex-direction: column;
    gap: 0;
  }
  .confirm__head {
    margin: calc(var(--space-4) * -1);
    margin-bottom: 0;
    padding: var(--space-5);
    border-bottom: 1px solid var(--border);
    background: linear-gradient(120deg, var(--surface) 30%, var(--accent-soft));
  }
  .confirm__step {
    margin: 0 0 var(--space-2);
    text-transform: uppercase;
    letter-spacing: 0.13em;
    font-size: 0.65rem;
    font-weight: 800;
    color: var(--destructive);
  }
  .confirm__title {
    margin: 0;
    font-family: var(--font-display, Georgia, serif);
    font-size: clamp(1.45rem, 2.5vw, 2rem);
    letter-spacing: -0.03em;
  }
  .confirm__sub { margin: var(--space-2) 0 0; }
  .confirm__section {
    padding: var(--space-5) 0;
    border-bottom: 1px solid var(--border);
  }
  .confirm__section-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-3);
  }
  .confirm__section-title h4 { font-size: 0.9rem; }
  .confirm__section-title span {
    color: var(--muted-foreground);
    font-size: 0.7rem;
  }
  .confirm__actions {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
    padding-top: var(--space-5);
  }
  .confirm__required {
    margin: calc(var(--space-2) * -1) 0 0;
    text-align: right;
    color: var(--destructive);
    font-size: 0.8rem;
  }
  .confirm__reuse {
    margin: var(--space-4) 0 0;
    padding: var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--warning-soft);
    color: color-mix(in srgb, var(--warning) 75%, var(--foreground));
    font-size: 0.8rem;
  }
  .confirm__export-hint { margin: var(--space-2) 0 0; }
  @media (max-width: 640px) {
    .confirm__head { padding: var(--space-4); }
    .confirm__actions > :global(*) { width: 100%; }
  }
</style>

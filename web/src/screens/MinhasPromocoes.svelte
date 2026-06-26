<script lang="ts">
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import PromotionList from '../lib/components/PromotionList.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import { untrack } from 'svelte';
  import { getPromotions, ApiError, type PromotionSummary } from '../lib/api';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { Whoami } from '../lib/types';

  interface Props {
    who: Whoami | null;
    promotion: Promotion;
    /** Open a promotion's detail (deep-link `#/promocoes/:id`). */
    onOpen: (summary: PromotionSummary) => void;
    /** Route param `:id` — when set, show the promotion detail instead of the list. */
    detailId?: string;
    /** Return to the list. */
    onBack: () => void;
  }
  let { who, promotion, onOpen, detailId, onBack }: Props = $props();

  // Admins/Stewards may view ALL promotions (server-enforced); everyone else only their own.
  let scope = $state<'mine' | 'all'>('mine');
  let promotionsP = $state(getPromotions('mine'));

  function load(s: 'mine' | 'all') {
    scope = s;
    promotionsP = getPromotions(s);
  }

  // Detail mode: open the promotion by id from its STORED snapshot (no reviewer re-run). Track
  // loading so the detail shows a skeleton until the snapshot renders.
  let detailLoading = $state(false);
  $effect(() => {
    const id = detailId;
    if (!id) return;
    // untrack the open call: openById's guard READS promotion.promotionId/phase and the call WRITES
    // them — without untrack the effect would track those reads and loop. We only want to re-open
    // when the route's :id changes.
    untrack(() => {
      detailLoading = true;
      promotion.openById(id).finally(() => (detailLoading = false));
    });
  });
</script>

{#if detailId}
  <div class="stack">
    <button class="back" type="button" onclick={onBack}>← Voltar para as promoções</button>
    {#if detailLoading && promotion.phase === 'idle'}
      <Card>
        <div class="stack">
          <Skeleton height="2.4rem" />
          <Skeleton height="8rem" />
        </div>
      </Card>
    {:else if promotion.phase === 'error'}
      <Card>
        <div class="error-state" role="alert">
          <span class="error">Não foi possível abrir a promoção: {promotion.error}</span>
          <Button variant="outline" onclick={() => detailId && promotion.openById(detailId)}>
            Tentar novamente
          </Button>
        </div>
      </Card>
    {:else}
      <PromotionReview {promotion} userEmail={who?.email ?? null} />
    {/if}
  </div>
{:else}
  <Card
    title="Minhas promoções"
    subtitle="Histórico de promoções — abra uma para ver a revisão e a trilha de auditoria."
  >
    {#if who?.is_admin}
      <div class="scope" role="group" aria-label="Escopo">
        <Button variant={scope === 'mine' ? 'primary' : 'outline'} onclick={() => load('mine')}>Minhas</Button>
        <Button variant={scope === 'all' ? 'primary' : 'outline'} onclick={() => load('all')}>Todas (Steward/Admin)</Button>
      </div>
    {/if}

    {#await promotionsP}
      <div class="stack">
        <Skeleton height="3.2rem" />
        <Skeleton height="3.2rem" width="80%" />
      </div>
    {:then promotions}
      <PromotionList {promotions} {onOpen} />
    {:catch err}
      <div class="error-state" role="alert">
        <span class="error">
          {#if err instanceof ApiError && err.status === 401}
            Sessão expirada — recarregue para reautenticar.
          {:else if err instanceof ApiError && err.status === 403}
            Sem permissão para ver todas as promoções — contacte o administrador.
          {:else}
            Não foi possível carregar as promoções: {err instanceof Error ? err.message : String(err)}
          {/if}
        </span>
        <Button variant="outline" onclick={() => load(scope)}>Tentar novamente</Button>
      </div>
    {/await}
  </Card>
{/if}

<style>
  .scope {
    display: flex;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
  }
  .back {
    align-self: flex-start;
    appearance: none;
    border: none;
    background: transparent;
    color: var(--accent-hover);
    font-family: inherit;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    padding: 0;
  }
  .back:hover {
    text-decoration: underline;
  }
  .back:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: var(--radius-sm);
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

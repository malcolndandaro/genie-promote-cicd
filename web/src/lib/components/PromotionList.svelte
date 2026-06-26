<script lang="ts">
  import PromotionCard from './PromotionCard.svelte';
  import type { PromotionSummary } from '../api';

  interface Props {
    promotions: PromotionSummary[];
    onOpen: (p: PromotionSummary) => void;
    /** Cap the rows shown (newest-first as provided). Home shows a few; history shows all. */
    limit?: number;
    emptyTitle?: string;
    emptyHint?: string;
  }
  let {
    promotions,
    onOpen,
    limit,
    emptyTitle = 'Nenhuma promoção ainda',
    emptyHint = 'Solicite uma promoção em “Meus espaços” para começar o histórico.',
  }: Props = $props();

  const shown = $derived(limit != null && limit >= 0 ? promotions.slice(0, limit) : promotions);
</script>

{#if promotions.length === 0}
  <div class="empty">
    <p class="empty__title">{emptyTitle}</p>
    <p class="muted text-sm">{emptyHint}</p>
  </div>
{:else}
  <ul class="promo-list">
    {#each shown as p (p.id)}
      <PromotionCard promotion={p} {onOpen} />
    {/each}
  </ul>
{/if}

<style>
  .promo-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .empty {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-4) 0;
  }
  .empty__title {
    margin: 0;
    font-weight: 600;
  }
</style>

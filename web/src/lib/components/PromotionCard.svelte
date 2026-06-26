<script lang="ts">
  import Badge from './Badge.svelte';
  import Button from './Button.svelte';
  import StatusChip from './StatusChip.svelte';
  import { kindMeta } from '../resources';
  import type { PromotionSummary } from '../api';
  import type { ResourceKind } from '../types';

  interface Props {
    promotion: PromotionSummary;
    /** Open the promotion (renders its stored snapshot — no reviewer re-run). */
    onOpen: (p: PromotionSummary) => void;
  }
  let { promotion: p, onOpen }: Props = $props();

  function fmt(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime())
      ? iso
      : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }
</script>

<li class="promo">
  <div class="promo__main">
    <div class="promo__title">
      <Badge tone="accent">{kindMeta(p.resource_kind as ResourceKind)?.label ?? p.resource_kind}</Badge>
      <span class="promo__name" title={p.resource_title ?? p.resource_id}>
        {p.resource_title ?? p.resource_id}
      </span>
    </div>
    <div class="promo__meta">
      <StatusChip phase={p.current_phase} />
      {#if p.pr_number}
        <a class="promo__pr" href={p.pr_url ?? '#'} target="_blank" rel="noopener noreferrer">
          PR #{p.pr_number} ↗
        </a>
      {/if}
      <time class="muted text-xs">{fmt(p.updated_at)}</time>
    </div>
  </div>
  <Button
    variant="outline"
    onclick={() => onOpen(p)}
    ariaLabel={`Abrir promoção: ${p.resource_title ?? p.resource_id}`}
  >
    Abrir →
  </Button>
</li>

<style>
  .promo {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    transition:
      border-color 0.15s ease,
      box-shadow 0.15s ease;
  }
  .promo:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-sm);
  }
  .promo__main {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    min-width: 0;
  }
  .promo__title {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
  }
  .promo__name {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .promo__meta {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .promo__pr {
    font-weight: 600;
    color: var(--accent-hover);
    white-space: nowrap;
  }
</style>

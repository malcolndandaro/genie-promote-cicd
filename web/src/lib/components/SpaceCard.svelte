<script lang="ts">
  import Badge from './Badge.svelte';
  import Button from './Button.svelte';
  import Icon from './Icon.svelte';
  import { kindMeta } from '../resources';
  import type { PromotableResource } from '../types';

  interface Props {
    resource: PromotableResource;
    /** Start a promotion for this resource. */
    onPromote: (resource: PromotableResource) => void;
    /** THIS card's promotion is in flight (button shows a spinner). */
    busy?: boolean;
    /** Another promotion is in flight — disable to avoid a concurrent request. */
    disabled?: boolean;
    /** THIS card is the one currently selected in the confirmation step (G3) — highlighted even
     * while disabled, so it stays clear which space the panel below is bound to. */
    selected?: boolean;
    /** Open this Space's most recent stored promotion in the contextual panel. */
    onOpen?: () => void;
  }
  let {
    resource, onPromote, busy = false, disabled = false, selected = false, onOpen,
  }: Props = $props();
</script>

<article class="space-card" class:space-card--selected={selected}>
  <button
    type="button"
    class="space-card__preview"
    onclick={onOpen}
    disabled={!onOpen}
    aria-label={onOpen ? `Abrir promoção mais recente: ${resource.title}` : undefined}
  >
    <div class="space-card__top">
      <span class="space-card__icon" aria-hidden="true"><Icon name="grid" size={18} /></span>
      <div class="space-card__badges">
        {#if resource.env}
          <Badge tone={resource.env === 'prod' ? 'success' : 'neutral'}>
            {resource.env === 'prod' ? 'prod' : 'dev'}
          </Badge>
        {/if}
        <Badge tone="accent">{kindMeta(resource.kind).label}</Badge>
      </div>
    </div>
    <h3 class="space-card__title" title={resource.title}>{resource.title}</h3>
  </button>
  <div class="space-card__foot">
    <Button
      onclick={() => onPromote(resource)}
      {disabled}
      loading={busy}
      ariaLabel={busy ? undefined : `Solicitar promoção: ${resource.title}`}
    >
      {busy ? 'Solicitando…' : 'Solicitar promoção →'}
    </Button>
  </div>
</article>

<style>
  .space-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow-sm);
    transition:
      border-color 0.15s ease,
      box-shadow 0.15s ease,
      transform 0.1s ease;
  }
  .space-card:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-md);
    transform: translateY(-2px);
  }
  .space-card--selected {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-soft);
  }
  .space-card__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }
  .space-card__preview {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    width: 100%;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
  }
  .space-card__preview:not(:disabled) { cursor: pointer; }
  .space-card__preview:disabled { cursor: default; }
  .space-card__preview:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 4px;
    border-radius: var(--radius-sm);
  }
  .space-card__badges {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .space-card__icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border-radius: var(--radius-sm);
    background: var(--accent-soft);
    color: var(--accent-hover);
  }
  .space-card__title {
    font-size: 1rem;
    line-height: 1.35;
    /* clamp long titles to two lines so the grid stays even */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .space-card__foot {
    margin-top: auto;
  }
</style>

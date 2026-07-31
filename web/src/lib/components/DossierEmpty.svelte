<script lang="ts">
  // The shared empty/placeholder state for both promotion surfaces.
  //
  // Used for two situations, which is why the copy is entirely the caller's: nothing in the register
  // at all, and nothing matching the current search. Each caller passes its own kind's wording so a
  // painel is never described as a Space.
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  import type { IconName } from './Icon.svelte';

  interface Props {
    icon: IconName;
    title: string;
    /** Supporting sentence — what to do next, in the caller's vocabulary. */
    hint?: string;
    children?: Snippet;
  }
  let { icon, title, hint, children }: Props = $props();
</script>

<div class="empty">
  <span class="empty__icon" aria-hidden="true"><Icon name={icon} size={20} /></span>
  <p class="empty__title">{title}</p>
  {#if hint}<p class="empty__hint">{hint}</p>{/if}
  {#if children}<div class="empty__action">{@render children()}</div>{/if}
</div>

<style>
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: var(--space-2);
    padding: var(--space-6) var(--space-4);
  }
  .empty__icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2.25rem;
    height: 2.25rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    color: var(--muted-foreground);
  }
  .empty__title {
    margin: 0;
    font-size: 0.9375rem;
    font-weight: 600;
  }
  .empty__hint {
    margin: 0;
    max-width: 42ch;
    color: var(--muted-foreground);
    font-size: 0.8125rem;
    line-height: 1.5;
  }
  .empty__action {
    margin-top: var(--space-2);
  }
</style>

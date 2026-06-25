<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    title?: string;
    subtitle?: string;
    class?: string;
    children: Snippet;
    actions?: Snippet;
  }

  let { title, subtitle, class: cls = '', children, actions }: Props = $props();
</script>

<section class={['card', cls]}>
  {#if title || actions}
    <header class="card__header">
      <div>
        {#if title}<h2 class="card__title">{title}</h2>{/if}
        {#if subtitle}<p class="card__subtitle">{subtitle}</p>{/if}
      </div>
      {#if actions}<div class="card__actions">{@render actions()}</div>{/if}
    </header>
  {/if}
  <div class="card__body">
    {@render children()}
  </div>
</section>

<style>
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    overflow: hidden;
  }
  .card__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-5) var(--space-5) 0;
  }
  .card__title {
    font-size: 1.1rem;
  }
  .card__subtitle {
    margin: 0.25rem 0 0;
    font-size: 0.85rem;
    color: var(--muted-foreground);
  }
  .card__actions {
    flex-shrink: 0;
  }
  .card__body {
    padding: var(--space-5);
  }
</style>

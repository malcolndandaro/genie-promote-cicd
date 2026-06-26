<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    tone?: 'neutral' | 'accent' | 'success' | 'warning' | 'destructive';
    children: Snippet;
  }

  let { tone = 'neutral', children }: Props = $props();
</script>

<span class={['badge', `badge--${tone}`]}>{@render children()}</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.35em;
    padding: 0.2rem 0.6rem;
    border-radius: var(--radius-pill);
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .badge--neutral {
    background: var(--surface-inset);
    color: var(--muted-foreground);
  }
  .badge--accent {
    background: var(--accent-soft);
    color: var(--accent-hover);
  }
  .badge--success {
    background: var(--success-soft);
    color: var(--success);
  }
  .badge--warning {
    background: var(--warning-soft);
    color: var(--warning);
  }
  .badge--destructive {
    background: var(--destructive-soft);
    color: var(--destructive);
  }
</style>

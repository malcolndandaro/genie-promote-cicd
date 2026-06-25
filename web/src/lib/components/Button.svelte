<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    variant?: 'primary' | 'accent' | 'outline' | 'ghost';
    type?: 'button' | 'submit';
    disabled?: boolean;
    loading?: boolean;
    onclick?: (e: MouseEvent) => void;
    class?: string;
    ariaDescribedby?: string;
    children: Snippet;
  }

  let {
    variant = 'primary',
    type = 'button',
    disabled = false,
    loading = false,
    onclick,
    class: cls = '',
    ariaDescribedby,
    children,
  }: Props = $props();
</script>

<button
  {type}
  {onclick}
  disabled={disabled || loading}
  aria-describedby={ariaDescribedby}
  class={['btn', `btn--${variant}`, loading && 'btn--loading', cls]}
>
  {#if loading}<span class="btn__spinner" aria-hidden="true"></span>{/if}
  {@render children()}
</button>

<style>
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    padding: 0.6rem 1.25rem;
    border: 1px solid transparent;
    border-radius: var(--radius-pill);
    font-family: inherit;
    font-size: 0.9rem;
    font-weight: 500;
    line-height: 1;
    cursor: pointer;
    transition:
      background-color 0.15s ease,
      border-color 0.15s ease,
      transform 0.05s ease,
      box-shadow 0.15s ease;
  }
  .btn:active:not(:disabled) {
    transform: translateY(1px);
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .btn--primary {
    background: var(--primary);
    color: var(--primary-foreground);
  }
  .btn--primary:hover:not(:disabled) {
    background: var(--primary-hover);
    box-shadow: var(--shadow-sm);
  }

  .btn--accent {
    background: var(--accent);
    color: var(--accent-foreground);
  }
  .btn--accent:hover:not(:disabled) {
    background: var(--accent-hover);
    box-shadow: var(--shadow-sm);
  }

  .btn--outline {
    background: transparent;
    border-color: var(--border-strong);
    color: var(--foreground);
  }
  .btn--outline:hover:not(:disabled) {
    border-color: var(--primary);
  }

  .btn--ghost {
    background: transparent;
    color: var(--muted-foreground);
  }
  .btn--ghost:hover:not(:disabled) {
    background: var(--surface-inset);
    color: var(--foreground);
  }

  .btn__spinner {
    width: 0.85em;
    height: 0.85em;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    animation: btn-spin 0.6s linear infinite;
  }
  @keyframes btn-spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>

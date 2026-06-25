<script lang="ts">
  interface Props {
    label?: string;
  }
  let { label }: Props = $props();
</script>

<span class="spinner-wrap" role="status">
  <span class="spinner" aria-hidden="true"></span>
  {#if label}<span class="muted text-sm">{label}</span>{/if}
</span>

<style>
  .spinner-wrap {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }
  .spinner {
    width: 1.1rem;
    height: 1.1rem;
    border: 2px solid var(--border-strong);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>

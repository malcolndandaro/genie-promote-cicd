<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    title?: string;
    subtitle?: string;
    right?: Snippet;
  }

  let {
    title = 'Promotor de Genie/AI-BI',
    subtitle = 'Autoria no dev → revisão automatizada → promoção governada para produção',
    right,
  }: Props = $props();
</script>

<header class="app-header">
  <div class="container app-header__inner">
    <div class="brand">
      <span class="brand__mark" aria-hidden="true"></span>
      <div class="brand__text">
        <span class="brand__title">{title}</span>
        <span class="brand__subtitle">{subtitle}</span>
      </div>
    </div>
    {#if right}
      <div class="app-header__right">{@render right()}</div>
    {/if}
  </div>
</header>

<style>
  .app-header {
    background: var(--primary);
    color: var(--primary-foreground);
    border-bottom: 3px solid var(--accent);
  }
  .app-header__inner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    padding-top: var(--space-4);
    padding-bottom: var(--space-4);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-width: 0;
  }
  /* A simple, license-safe brand glyph echoing the CERC teal accent — not the CERC logo. */
  .brand__mark {
    flex-shrink: 0;
    width: 1.6rem;
    height: 1.6rem;
    border-radius: 8px;
    background: var(--accent);
    box-shadow: inset 0 0 0 4px var(--primary), 0 0 0 1px var(--accent);
  }
  .brand__text {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .brand__title {
    font-weight: 700;
    font-size: 1.05rem;
    letter-spacing: -0.01em;
  }
  .brand__subtitle {
    font-size: 0.78rem;
    color: rgba(255, 255, 255, 0.7);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .app-header__right {
    flex-shrink: 0;
  }
  @media (max-width: 640px) {
    .brand__subtitle {
      display: none;
    }
  }
</style>

<script lang="ts">
  // The framed register that holds DossierRow entries, plus its column header.
  //
  // The header names what each column MEANS, which is what turns a list of rows into a register an
  // author can read. It hides below the shell breakpoint, where rows stack and the labels would
  // describe a layout that no longer exists.
  import type { Snippet } from 'svelte';

  interface Props {
    /** Accessible name for the register region. */
    label: string;
    /** Column header for the identity column, in the caller's own vocabulary. */
    identHeader: string;
    children: Snippet;
  }
  let { label, identHeader, children }: Props = $props();
</script>

<section class="index" aria-label={label}>
  <header class="index__head">
    <span class="index__col index__col--ident">{identHeader}</span>
    <span class="index__col index__col--standing">Situação</span>
    <span class="index__col index__col--action">Ação</span>
  </header>
  {@render children()}
</section>

<style>
  .index {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    overflow: hidden;
  }
  .index__head {
    display: grid;
    /* Mirrors DossierRow's grid: glyph gutter + identity + standing + action. */
    grid-template-columns: 1.75rem minmax(0, 1fr) auto auto;
    gap: var(--space-3);
    padding: 0.5rem var(--space-4);
    background: var(--surface-inset);
    border-bottom: 1px solid var(--border);
  }
  .index__col {
    color: var(--muted-foreground);
    font-size: 0.6875rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .index__col--ident {
    grid-column: 2;
  }
  .index__col--standing {
    text-align: right;
  }
  .index__col--action {
    /* Reserves the action column's width so the header rule aligns with the buttons below. */
    min-width: 9.5rem;
    text-align: right;
  }
  @media (max-width: 860px) {
    .index__head {
      display: none;
    }
  }
</style>

<script lang="ts">
  // The shared index header for both promotion surfaces (Genie Spaces and AI/BI painéis).
  //
  // One grammar, kind-specific content: the search field, the register count and the optional
  // filter/scope controls sit in the same place on both pages, so an author who learns one page can
  // read the other. What differs is only the WORDS the caller passes (a Space is counted as a
  // Space, a painel as a painel) — never the layout.
  //
  // The count is a plain sentence, not a hero metric: this is a register, and the register's size is
  // context for scanning, not a KPI to admire.
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';

  interface Props {
    /** Bound search text. */
    query: string;
    /** Placeholder for the search field, in the caller's own vocabulary. */
    searchLabel: string;
    /** The field's accessible name — short, e.g. "Buscar Space". Distinct from the longer
     * placeholder so screen-reader users get a terse name rather than a sentence. */
    searchName: string;
    /** The full register sentence, e.g. "3 Spaces disponíveis em Dev". Caller owns pluralization. */
    countLabel: string;
    /** Optional filter/scope controls, rendered after the count. */
    controls?: Snippet;
  }
  let { query = $bindable(), searchLabel, searchName, countLabel, controls }: Props = $props();
</script>

<div class="toolbar">
  <div class="toolbar__search">
    <span class="toolbar__search-icon" aria-hidden="true"><Icon name="search" size={15} /></span>
    <!-- Deliberately `type="text"`, not `type="search"`: the latter maps to the `searchbox` role and
         adds a UA-drawn clear affordance that fights the register's own chrome. -->
    <input type="text" bind:value={query} placeholder={searchLabel} aria-label={searchName} />
  </div>

  <p class="toolbar__count tnum">{countLabel}</p>

  {#if controls}
    <div class="toolbar__controls">{@render controls()}</div>
  {/if}
</div>

<style>
  .toolbar {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .toolbar__search {
    position: relative;
    display: flex;
    align-items: center;
    flex: 1 1 18rem;
    min-width: 0;
  }
  .toolbar__search-icon {
    position: absolute;
    left: 0.6rem;
    display: flex;
    color: var(--muted-foreground);
    pointer-events: none;
  }
  .toolbar__search input {
    width: 100%;
    min-width: 0;
    padding: 0.45rem 0.65rem 0.45rem 2rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    color: inherit;
    font: inherit;
    font-size: 0.875rem;
  }
  .toolbar__search input::placeholder {
    color: var(--muted-foreground);
  }
  .toolbar__count {
    margin: 0;
    color: var(--muted-foreground);
    font-size: 0.8125rem;
    white-space: nowrap;
  }
  .toolbar__controls {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-left: auto;
  }
  @media (max-width: 640px) {
    .toolbar__search {
      flex-basis: 100%;
    }
    .toolbar__controls {
      margin-left: 0;
    }
  }
</style>

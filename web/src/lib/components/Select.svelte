<script lang="ts">
  export interface Option {
    value: string;
    label: string;
  }

  interface Props {
    options: Option[];
    value: string;
    id?: string;
    placeholder?: string;
    ariaLabel?: string;
    disabled?: boolean;
    onchange?: (value: string) => void;
  }

  let {
    options,
    value = $bindable(),
    id,
    placeholder,
    ariaLabel,
    disabled = false,
    onchange,
  }: Props = $props();

  function handle(e: Event & { currentTarget: HTMLSelectElement }) {
    value = e.currentTarget.value;
    onchange?.(value);
  }
</script>

<div class="select">
  <select {id} {value} onchange={handle} aria-label={ariaLabel} {disabled}>
    {#if placeholder}
      <option value="" disabled selected={value === ''}>{placeholder}</option>
    {/if}
    {#each options as o (o.value)}
      <option value={o.value}>{o.label}</option>
    {/each}
  </select>
  <span class="select__chevron" aria-hidden="true">▾</span>
</div>

<style>
  .select {
    position: relative;
    display: inline-flex;
    width: 100%;
  }
  select {
    appearance: none;
    width: 100%;
    font-family: inherit;
    font-size: 0.9rem;
    color: var(--foreground);
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    padding: 0.6rem 2.25rem 0.6rem 0.9rem;
    cursor: pointer;
    transition:
      border-color 0.15s ease,
      box-shadow 0.15s ease;
  }
  select:hover:not(:disabled) {
    border-color: var(--primary);
  }
  select:focus-visible {
    border-color: var(--ring);
    box-shadow: 0 0 0 3px var(--accent-soft);
    outline: none;
  }
  select:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .select__chevron {
    position: absolute;
    right: 0.85rem;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
    color: var(--muted-foreground);
    font-size: 0.8rem;
  }
</style>

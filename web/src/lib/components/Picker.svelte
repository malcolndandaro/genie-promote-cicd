<script lang="ts">
  // G1 — the ONE reusable prefilled/searchable picker. Every field that used to accept a raw typed
  // id/email/username (space pickers, audience principals and role emails)
  // renders through this component instead: free text is ONLY a search query fed to `search`; the
  // value(s) this component hands back (`value`/`values`) are always options `search` itself
  // returned, never the raw typed string. Pure filter/selection logic lives in `../picker.ts` (unit-
  // tested there — this file is the async/DOM half: loading, debounce, keyboard nav, chips).
  import type { PickerOption } from '../picker';

  interface Props {
    label: string;
    placeholder?: string;
    mode?: 'single' | 'multi';
    /** Fetches the options matching a query (blank query = the prefilled default page). May throw —
     * surfaced as the panel's error state, never an uncaught rejection. */
    search: (query: string) => Promise<PickerOption[]>;
    value?: PickerOption | null;
    values?: PickerOption[];
    disabled?: boolean;
    debounceMs?: number;
  }

  let {
    label,
    placeholder = 'Buscar…',
    mode = 'single',
    search,
    value = $bindable(null),
    values = $bindable([]),
    disabled = false,
    debounceMs = 250,
  }: Props = $props();

  type Status = 'idle' | 'loading' | 'ready' | 'error';

  let open = $state(false);
  let query = $state('');
  let status = $state<Status>('idle');
  let options = $state<PickerOption[]>([]);
  let errorMsg = $state<string | null>(null);
  let activeIndex = $state(-1);
  let containerEl: HTMLDivElement | undefined = $state();
  let inputEl: HTMLInputElement | undefined = $state();
  let debounceTimer: ReturnType<typeof setTimeout> | undefined;
  const panelId = `picker-panel-${Math.random().toString(36).slice(2)}`;
  let seq = 0; // guards a stale (superseded) search response from overwriting a newer one

  function isPicked(o: PickerOption): boolean {
    return mode === 'multi' ? values.some((v) => v.id === o.id) : value?.id === o.id;
  }

  async function runSearch(q: string): Promise<void> {
    const mySeq = ++seq;
    status = 'loading';
    errorMsg = null;
    try {
      const result = await search(q);
      if (mySeq !== seq) return; // a newer query already superseded this one
      options = result;
      status = 'ready';
      activeIndex = result.length > 0 ? 0 : -1;
    } catch (e) {
      if (mySeq !== seq) return;
      status = 'error';
      errorMsg = e instanceof Error ? e.message : String(e);
    }
  }

  function onQueryInput(): void {
    activeIndex = -1;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => void runSearch(query), debounceMs);
  }

  function openPicker(): void {
    if (disabled || open) return;
    open = true;
    if (status === 'idle') void runSearch(''); // prefilled default page, no query typed yet
  }

  function closePicker(): void {
    open = false;
    activeIndex = -1;
  }

  function pick(o: PickerOption): void {
    if (mode === 'multi') {
      values = isPicked(o) ? values.filter((v) => v.id !== o.id) : [...values, o];
      query = '';
      void runSearch('');
      inputEl?.focus(); // stay open + focused so picking several principals in a row needs no re-click
    } else {
      value = o;
      query = '';
      closePicker();
    }
  }

  function removeChip(o: PickerOption): void {
    values = values.filter((v) => v.id !== o.id);
  }

  function clearSingle(): void {
    value = null;
  }

  function onFocusOut(e: FocusEvent): void {
    const next = e.relatedTarget as Node | null;
    if (next && containerEl?.contains(next)) return; // focus moved WITHIN the picker — stay open
    closePicker();
  }

  function onKeydown(e: KeyboardEvent): void {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter') {
        e.preventDefault();
        openPicker();
      }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, options.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIndex >= 0 && activeIndex < options.length) pick(options[activeIndex]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closePicker();
    }
  }

  // --- panel positioning (found live: Card's `overflow: hidden` clipped the absolute panel) ---
  // The panel renders `position: fixed`, anchored to the control's viewport rect, so NO ancestor
  // (Card, modal, table) can ever clip it. Repositioned while open on scroll (capture — inner
  // containers too), resize, and chip changes (chips shift the control down).
  let controlEl = $state<HTMLDivElement | null>(null);
  let panelStyle = $state('');

  function positionPanel() {
    if (!controlEl) return;
    const r = controlEl.getBoundingClientRect();
    panelStyle = `top:${r.bottom + 4}px; left:${r.left}px; width:${r.width}px;`;
  }

  $effect(() => {
    if (!open) return;
    void values.length; // chips above the control shift it — re-anchor
    positionPanel();
  });
</script>

<svelte:window onresize={() => open && positionPanel()} />
<svelte:document onscrollcapture={() => open && positionPanel()} />

<div class="picker" bind:this={containerEl} onfocusout={onFocusOut}>
  <span class="picker__label">{label}</span>

  {#if mode === 'multi' && values.length > 0}
    <ul class="picker__chips">
      {#each values as v (v.id)}
        <li class="picker__chip">
          <span>{v.label}</span>
          <button
            type="button"
            class="picker__chip-remove"
            onclick={() => removeChip(v)}
            aria-label={`Remover ${v.label}`}
            {disabled}
          >
            ✕
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  <div class="picker__control" bind:this={controlEl}>
    {#if mode === 'single' && value && !open}
      <button type="button" class="picker__selected" onclick={openPicker} {disabled}>
        <span>{value.label}</span>
        {#if value.sublabel}<span class="picker__sublabel">{value.sublabel}</span>{/if}
      </button>
      <button
        type="button"
        class="picker__clear"
        onclick={clearSingle}
        aria-label="Limpar seleção"
        {disabled}
      >
        ✕
      </button>
    {:else}
      <input
        bind:this={inputEl}
        bind:value={query}
        type="text"
        class="picker__input"
        role="combobox"
        aria-expanded={open}
        aria-controls={panelId}
        aria-autocomplete="list"
        aria-label={label}
        {placeholder}
        {disabled}
        oninput={onQueryInput}
        onfocus={openPicker}
        onkeydown={onKeydown}
      />
    {/if}
  </div>

  {#if open}
    <ul id={panelId} class="picker__panel" role="listbox" aria-label={label} style={panelStyle}>
      {#if status === 'loading'}
        <li class="picker__state" aria-busy="true">Buscando…</li>
      {:else if status === 'error'}
        <li class="picker__state picker__state--error" role="alert">Erro ao buscar: {errorMsg}</li>
      {:else if options.length === 0}
        <li class="picker__state">Nenhum resultado.</li>
      {:else}
        {#each options as o, i (o.id)}
          <li>
            <button
              type="button"
              role="option"
              aria-selected={isPicked(o)}
              class={[
                'picker__option',
                i === activeIndex && 'picker__option--active',
                isPicked(o) && 'picker__option--picked',
              ]}
              onclick={() => pick(o)}
            >
              <span class="picker__option-label">{o.label}</span>
              {#if o.sublabel}<span class="picker__sublabel">{o.sublabel}</span>{/if}
              {#if isPicked(o)}<span class="picker__check" aria-hidden="true">✓</span>{/if}
            </button>
          </li>
        {/each}
      {/if}
    </ul>
  {/if}
</div>

<style>
  .picker {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .picker__label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .picker__chips {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .picker__chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: var(--accent-soft);
    color: var(--accent-hover);
    border-radius: var(--radius-pill);
    padding: 0.25rem 0.4rem 0.25rem 0.75rem;
    font-size: 0.8rem;
    font-weight: 600;
  }
  .picker__chip-remove {
    appearance: none;
    border: none;
    background: none;
    color: inherit;
    cursor: pointer;
    line-height: 1;
    padding: 0.15rem;
    border-radius: 50%;
    font-size: 0.7rem;
  }
  .picker__chip-remove:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.08);
  }
  .picker__control {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .picker__input {
    flex: 1 1 auto;
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
    background: var(--surface);
    color: inherit;
  }
  .picker__input:focus {
    outline: 2px solid var(--ring);
    outline-offset: 1px;
  }
  .picker__selected {
    flex: 1 1 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .picker__selected:hover:not(:disabled) {
    border-color: var(--border-strong);
  }
  .picker__clear {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted-foreground);
    border-radius: var(--radius-sm);
    width: 2rem;
    height: 2.1rem;
    cursor: pointer;
    flex: 0 0 auto;
  }
  .picker__clear:hover:not(:disabled) {
    background: var(--surface-inset);
    color: var(--foreground);
  }
  .picker__panel {
    /* fixed + inline top/left/width from positionPanel(): immune to any clipping ancestor
       (Card has overflow:hidden for its rounded corners — this is what cut the panel off). */
    position: fixed;
    z-index: 50;
    margin: 0;
    padding: 0.3rem;
    list-style: none;
    max-height: 15rem;
    overflow-y: auto;
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
  }
  .picker__state {
    padding: 0.6rem 0.75rem;
    font-size: 0.85rem;
    color: var(--muted-foreground);
  }
  .picker__state--error {
    color: var(--destructive);
  }
  .picker__option {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: 0.5rem 0.65rem;
    border: none;
    background: none;
    border-radius: var(--radius-sm);
    font: inherit;
    text-align: left;
    cursor: pointer;
    color: inherit;
  }
  .picker__option-label {
    flex: 1 1 auto;
  }
  .picker__option--active {
    background: var(--surface-inset);
  }
  .picker__option--picked {
    color: var(--accent-hover);
    font-weight: 600;
  }
  .picker__sublabel {
    font-size: 0.75rem;
    color: var(--muted-foreground);
    white-space: nowrap;
  }
  .picker__check {
    color: var(--accent-hover);
    font-weight: 700;
  }
</style>

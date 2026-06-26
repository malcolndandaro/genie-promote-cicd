<script lang="ts">
  export interface Tab {
    value: string;
    label: string;
  }

  interface Props {
    tabs: Tab[];
    active: string;
    /** Prefix for tab/panel ids so the panel can set aria-labelledby={`${idBase}-tab-<value>`}. */
    idBase?: string;
  }

  let { tabs, active = $bindable(), idBase = 'tabs' }: Props = $props();

  let tablistEl: HTMLDivElement | undefined = $state();

  const tabId = (value: string) => `${idBase}-tab-${value}`;
  const panelId = (value: string) => `${idBase}-panel-${value}`;

  function activate(value: string) {
    active = value;
    tablistEl?.querySelector<HTMLButtonElement>(`#${CSS.escape(tabId(value))}`)?.focus();
  }

  // ARIA tabs keyboard model (automatic activation: selection follows focus).
  function onKeydown(e: KeyboardEvent) {
    const i = tabs.findIndex((t) => t.value === active);
    if (i < 0) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      activate(tabs[(i + 1) % tabs.length].value);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      activate(tabs[(i - 1 + tabs.length) % tabs.length].value);
    } else if (e.key === 'Home') {
      e.preventDefault();
      activate(tabs[0].value);
    } else if (e.key === 'End') {
      e.preventDefault();
      activate(tabs[tabs.length - 1].value);
    }
  }
</script>

<div class="tabs" role="tablist" bind:this={tablistEl}>
  {#each tabs as t (t.value)}
    <button
      type="button"
      role="tab"
      id={tabId(t.value)}
      aria-controls={panelId(t.value)}
      aria-selected={active === t.value}
      tabindex={active === t.value ? 0 : -1}
      class={['tab', active === t.value && 'tab--active']}
      onclick={() => (active = t.value)}
      onkeydown={onKeydown}
    >
      {t.label}
    </button>
  {/each}
</div>

<style>
  .tabs {
    display: inline-flex;
    gap: var(--space-1);
    padding: 0.25rem;
    background: var(--surface-inset);
    border-radius: var(--radius-pill);
  }
  .tab {
    appearance: none;
    border: none;
    background: transparent;
    font-family: inherit;
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--muted-foreground);
    padding: 0.4rem 1rem;
    border-radius: var(--radius-pill);
    cursor: pointer;
    transition:
      background-color 0.15s ease,
      color 0.15s ease;
  }
  .tab:hover {
    color: var(--foreground);
  }
  .tab--active {
    background: var(--surface);
    color: var(--primary);
    box-shadow: var(--shadow-sm);
  }
</style>

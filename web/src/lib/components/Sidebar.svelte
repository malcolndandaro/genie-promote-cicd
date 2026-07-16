<script lang="ts" module>
  import type { RouteId } from '../route';
  import type { IconName } from './Icon.svelte';
  export interface NavItem {
    id: RouteId;
    label: string;
    icon: IconName;
  }
  /** A persona-scoped group of nav items (S2: persona-sectioned nav, PT1's winning Variant A —
   * grouped sections, always expanded, one per persona the caller holds). `title` is the
   * section heading; omit/leave undefined for a titleless group (not used today, but keeps the
   * shape flexible). */
  export interface NavSection {
    title?: string;
    items: NavItem[];
  }
</script>

<script lang="ts">
  import Icon from './Icon.svelte';
  import { formatHash } from '../route';

  interface Props {
    /** Grouped nav sections — one per persona the caller holds, rendered in order, all always
     * visible (union of capabilities, D1). Replaces the old flat `navItems` list. */
    sections: NavSection[];
    activeId: RouteId;
    /** Product name shown in the rail brand (generic — not a customer reference, ADR-0004). */
    product?: string;
    /** Mobile drawer open state (ignored on desktop, where the rail is always visible). */
    mobileOpen?: boolean;
    /** Called when a nav link is activated — lets the shell close the mobile drawer. */
    onNavigate?: () => void;
  }
  let {
    sections,
    activeId,
    product = 'Promotor Genie/AI-BI',
    mobileOpen = false,
    onNavigate,
  }: Props = $props();
</script>

<aside id="sidebar-nav" class="sidebar" class:sidebar--open={mobileOpen} aria-label="Barra lateral">
  <a class="brand" href={formatHash({ id: 'espacos' })} onclick={onNavigate}>
    <span class="brand__mark" aria-hidden="true"></span>
    <span class="brand__name">{product}</span>
  </a>

  <nav class="nav" aria-label="Navegação principal">
    {#each sections as section, i (section.title ?? i)}
      <div class="nav__group">
        {#if section.title}<h4 class="nav__group-title">{section.title}</h4>{/if}
        <ul class="nav__list">
          {#each section.items as item (item.id)}
            <li>
              <a
                class="nav__link"
                class:nav__link--active={item.id === activeId}
                href={formatHash({ id: item.id })}
                aria-current={item.id === activeId ? 'page' : undefined}
                onclick={onNavigate}
              >
                <Icon name={item.icon} size={18} />
                <span>{item.label}</span>
              </a>
            </li>
          {/each}
        </ul>
      </div>
    {/each}
  </nav>

  <div class="sidebar__foot">
    <span class="sidebar__foot-dot" aria-hidden="true"></span>
    Promoção governada dev → prod
  </div>
</aside>

<style>
  .sidebar {
    display: flex;
    flex-direction: column;
    width: var(--sidebar-w);
    height: 100vh;
    position: sticky;
    top: 0;
    background: var(--primary);
    color: var(--primary-foreground);
    border-right: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-5) var(--space-4);
    text-decoration: none;
    color: inherit;
    border-bottom: 1px solid var(--primary-border);
  }
  .brand__mark {
    flex-shrink: 0;
    width: 1.7rem;
    height: 1.7rem;
    border-radius: 8px;
    background: var(--accent);
    box-shadow: inset 0 0 0 4px var(--primary);
  }
  .brand__name {
    font-weight: 700;
    font-size: 0.98rem;
    line-height: 1.2;
    letter-spacing: -0.01em;
  }
  .nav {
    flex: 1;
    overflow-y: auto;
    padding: var(--space-4) var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .nav__group-title {
    margin: 0 0 var(--space-2) 0.75rem;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--primary-foreground-faint);
  }
  .nav__list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .nav__link {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 0.6rem 0.75rem;
    border-radius: var(--radius-sm);
    color: var(--primary-foreground-muted);
    text-decoration: none;
    font-size: 0.92rem;
    font-weight: 500;
    transition:
      background-color 0.15s ease,
      color 0.15s ease;
  }
  .nav__link:hover {
    background: var(--primary-surface-hover);
    color: var(--primary-foreground);
  }
  .nav__link--active {
    background: color-mix(in srgb, var(--accent) 22%, transparent);
    color: var(--primary-foreground);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .nav__link:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .sidebar__foot {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-4);
    font-size: 0.72rem;
    color: var(--primary-foreground-faint);
    border-top: 1px solid var(--primary-border);
  }
  .sidebar__foot-dot {
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 50%;
    background: var(--accent);
  }

  /* Mobile: the rail becomes an off-canvas drawer slid in by the shell's `mobileOpen`. */
  @media (max-width: 860px) {
    .sidebar {
      position: fixed;
      z-index: 50;
      transform: translateX(-100%);
      transition: transform 0.22s ease;
      box-shadow: var(--shadow-lg);
    }
    .sidebar--open {
      transform: translateX(0);
    }
  }
</style>

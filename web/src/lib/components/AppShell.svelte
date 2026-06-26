<script lang="ts">
  import type { Snippet } from 'svelte';
  import Sidebar from './Sidebar.svelte';
  import type { NavItem } from './Sidebar.svelte';
  import type { Route } from '../route';

  interface Props {
    navItems: NavItem[];
    route: Route;
    /** The top bar. Receives `toggle` (open/close the drawer) + `open` (so the hamburger can expose
     * `aria-expanded`). */
    header: Snippet<[{ toggle: () => void; open: boolean }]>;
    children: Snippet;
  }
  let { navItems, route, header, children }: Props = $props();

  // The shell owns the mobile drawer state; the rail is always visible on desktop.
  let mobileOpen = $state(false);
  const toggle = () => (mobileOpen = !mobileOpen);
  const close = () => (mobileOpen = false);

  // Close the drawer on any navigation (hash change) or Escape, so it never lingers over content.
  $effect(() => {
    const onHash = () => (mobileOpen = false);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') mobileOpen = false;
    };
    window.addEventListener('hashchange', onHash);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener('keydown', onKey);
    };
  });

  // Lock body scroll while the mobile drawer is open so the content behind the scrim doesn't scroll.
  $effect(() => {
    if (!mobileOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  });

  // Move focus to the content region on route change (not the first load) so screen-reader + keyboard
  // users land on the new screen instead of staying on the sidebar.
  let mainEl = $state<HTMLElement>();
  let firstRoute = true;
  $effect(() => {
    void route.id;
    void route.param; // track both so a deep-link param change also moves focus
    if (firstRoute) {
      firstRoute = false;
      return;
    }
    mainEl?.focus();
  });

  // The skip link targets the same region; an href="#…" would collide with the hash router, so move
  // focus directly instead of navigating.
  function skipToContent(e: MouseEvent): void {
    e.preventDefault();
    mainEl?.focus();
  }
</script>

<a class="skip-link" href="#conteudo" onclick={skipToContent}>Ir para o conteúdo</a>

<div class="shell">
  <Sidebar {navItems} activeId={route.id} {mobileOpen} onNavigate={close} />

  {#if mobileOpen}
    <button type="button" class="shell__scrim" aria-label="Fechar menu" onclick={close}></button>
  {/if}

  <div class="shell__main">
    {@render header({ toggle, open: mobileOpen })}
    <main bind:this={mainEl} id="conteudo" class="shell__content" tabindex="-1">
      <!-- While the mobile drawer is open, the content behind the scrim is inert so keyboard focus
           can't reach it (the scrim/Escape/nav-click all close the drawer). -->
      <div class="shell__inner" inert={mobileOpen}>
        {@render children()}
      </div>
    </main>
  </div>
</div>

<style>
  /* Keyboard skip link — hidden until focused, then pinned top-left over the chrome. */
  .skip-link {
    position: fixed;
    top: var(--space-3);
    left: var(--space-3);
    z-index: 100;
    padding: 0.5rem 0.85rem;
    background: var(--surface);
    color: var(--foreground);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-md);
    font-size: 0.85rem;
    font-weight: 600;
    text-decoration: none;
    transform: translateY(-150%);
    transition: transform 0.15s ease;
  }
  /* :focus-visible (not :focus) so a mouse click near the top never flashes the link into view. */
  .skip-link:focus-visible {
    transform: translateY(0);
  }
  /* Under reduced motion, reveal by opacity rather than a position jump. */
  @media (prefers-reduced-motion: reduce) {
    .skip-link {
      transform: translateY(0);
      opacity: 0;
    }
    .skip-link:focus-visible {
      opacity: 1;
    }
  }
  .shell {
    display: grid;
    grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
    min-height: 100vh;
  }
  .shell__main {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .shell__content {
    flex: 1;
    padding: var(--space-6) var(--space-5);
    outline: none;
  }
  .shell__inner {
    width: 100%;
    max-width: var(--content-max);
    margin: 0 auto;
  }
  .shell__scrim {
    display: none;
  }

  @media (max-width: 860px) {
    .shell {
      grid-template-columns: minmax(0, 1fr);
    }
    .shell__content {
      padding: var(--space-5) var(--space-4);
    }
    .shell__scrim {
      display: block;
      position: fixed;
      inset: 0;
      z-index: 45;
      border: 0;
      padding: 0;
      cursor: pointer;
      background: var(--scrim);
      animation: scrim-in 0.18s ease;
    }
    @keyframes scrim-in {
      from {
        opacity: 0;
      }
      to {
        opacity: 1;
      }
    }
  }
</style>

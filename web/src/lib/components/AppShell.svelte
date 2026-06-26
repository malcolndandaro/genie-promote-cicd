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
</script>

<div class="shell">
  <Sidebar {navItems} activeId={route.id} {mobileOpen} onNavigate={close} />

  {#if mobileOpen}
    <button type="button" class="shell__scrim" aria-label="Fechar menu" onclick={close}></button>
  {/if}

  <div class="shell__main">
    {@render header({ toggle, open: mobileOpen })}
    <main id="conteudo" class="shell__content" tabindex="-1">
      <div class="shell__inner">
        {@render children()}
      </div>
    </main>
  </div>
</div>

<style>
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

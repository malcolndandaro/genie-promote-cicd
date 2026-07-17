<script lang="ts">
  import Icon from './Icon.svelte';
  import Badge from './Badge.svelte';
  import type { Whoami } from '../types';

  interface Props {
    /** Current section title (from the active route). */
    title: string;
    who: Whoami | null;
    /** Mobile drawer open state — drives the hamburger's aria-expanded. */
    open: boolean;
    onToggle: () => void;
  }
  let { title, who, open, onToggle }: Props = $props();

  // The repo the GitHub icon links to comes from /whoami (config-driven, ADR-0004). The backend's
  // `_repo_url()` is the SINGLE source of truth; this constant is only a defensive fallback for the
  // brief pre-whoami window (or a mocked test that omits repo_url) and should mirror that default.
  // $derived (not const) so it updates when `who` resolves from null.
  const DEFAULT_REPO = 'https://github.com/malcolndandaro/genie-promote-cicd';
  const repoUrl = $derived(who?.repo_url || DEFAULT_REPO);

  // Identity-derived role label (display-only — the real SoD is GitHub's Environment gate). Steward
  // takes priority over Admin because it owns promotion governance.
  const role = $derived(
    who?.email && who?.steward && who.email.toLowerCase() === who.steward.toLowerCase()
      ? 'Steward'
      : who?.is_admin
        ? 'Admin'
        : null,
  );
</script>

<header class="topbar">
  <button
    class="topbar__menu"
    type="button"
    aria-label="Abrir menu"
    aria-expanded={open}
    aria-controls="sidebar-nav"
    onclick={onToggle}
  >
    <Icon name="menu" />
  </button>

  <h1 class="topbar__title">{title}</h1>

  <div class="topbar__right">
    {#if role}<Badge tone="accent">{role}</Badge>{/if}
    {#if who?.email}
      <span class="identity" title={who.email}>
        <span class="identity__dot" aria-hidden="true"></span>
        <span class="identity__email">{who.email}</span>
      </span>
    {/if}
    <a
      class="topbar__gh"
      href={repoUrl}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Abrir repositório no GitHub"
    >
      <Icon name="github" size={20} />
    </a>
  </div>
</header>

<style>
  .topbar {
    position: sticky;
    top: 0;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: var(--header-h);
    padding: 0 var(--space-5);
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }
  .topbar__title {
    font-size: 1.05rem;
    font-weight: 700;
  }
  .topbar__menu {
    display: none;
    appearance: none;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--foreground);
    border-radius: var(--radius-sm);
    padding: 0.35rem;
    cursor: pointer;
    line-height: 0;
  }
  .topbar__menu:hover {
    background: var(--surface-inset);
  }
  .topbar__right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }
  .identity {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.85rem;
    color: var(--muted-foreground);
  }
  .identity__email {
    max-width: 16rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .identity__dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--accent);
    flex-shrink: 0;
  }
  .topbar__gh {
    display: inline-flex;
    align-items: center;
    color: var(--muted-foreground);
    border-radius: var(--radius-sm);
    padding: 0.3rem;
    transition: color 0.15s ease;
  }
  .topbar__gh:hover {
    color: var(--foreground);
  }
  @media (max-width: 860px) {
    .topbar {
      padding: 0 var(--space-4);
    }
    .topbar__menu {
      display: inline-flex;
    }
    .identity__email {
      max-width: 8rem;
    }
  }
  @media (max-width: 520px) {
    .identity__email {
      display: none;
    }
  }
</style>

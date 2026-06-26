<script lang="ts">
  import AppShell from './lib/components/AppShell.svelte';
  import type { NavItem } from './lib/components/Sidebar.svelte';
  import Icon from './lib/components/Icon.svelte';
  import Card from './lib/components/Card.svelte';
  import Button from './lib/components/Button.svelte';
  import MeusEspacos from './screens/MeusEspacos.svelte';
  import MinhasPromocoes from './screens/MinhasPromocoes.svelte';
  import NovoEspaco from './screens/NovoEspaco.svelte';
  import { getWhoami, type PromotionSummary } from './lib/api';
  import { Promotion } from './lib/promotion.svelte';
  import { Router } from './lib/router.svelte';
  import type { RouteId } from './lib/route';
  import type { Whoami } from './lib/types';

  // The shared promotion flow state (selection → review → approval).
  const promotion = new Promotion();

  // Hash router — the URL is the single source of truth for the active screen (deep-links, reload,
  // browser back/forward, and the sidebar's plain <a href="#/…"> links all work for free).
  const router = new Router();

  // OBO identity (drives the header identity + the review AI-trust badge + SoD). Display-only, so a
  // failure is silent — the spaces error surfaces any re-auth need.
  let who = $state<Whoami | null>(null);
  getWhoami()
    .then((w) => {
      who = w;
      promotion.viewerEmail = w.email;
      promotion.requesterEmail = w.email;
      promotion.steward = w.steward;
      // Identity-derived role (no manual toggle): the viewer IS the Steward when their OBO email
      // matches the configured steward. The approval view follows this + whether the promotion is
      // theirs; the real SoD is still GitHub's gate.
      promotion.isSteward = !!(
        w.email && w.steward && w.email.toLowerCase() === w.steward.toLowerCase()
      );
    })
    .catch(() => {});

  // Recover-on-load (LB3): restore the most-recent promotion from its STORED snapshot — no reviewer
  // re-run. Best-effort + independent of whoami; the live status resumes via the polling effect.
  promotion.recover().catch(() => {});

  const NAV_ITEMS: NavItem[] = [
    { id: 'inicio', label: 'Início', icon: 'home' },
    { id: 'espacos', label: 'Meus espaços', icon: 'grid' },
    { id: 'promocoes', label: 'Minhas promoções', icon: 'git-branch' },
    { id: 'novo', label: '＋ Novo Genie Space', icon: 'plus-circle' },
  ];

  const SECTION_TITLE: Record<RouteId, string> = {
    inicio: 'Início',
    espacos: 'Meus espaços',
    promocoes: 'Minhas promoções',
    novo: 'Novo Genie Space',
  };

  // Open a promotion from the history list: load its stored snapshot + audit, then show it in the
  // review view (the "Meus espaços" panel renders the active promotion). No reviewer re-run.
  // (UI4 will deep-link this to #/promocoes/:id; UI1 preserves the existing in-place behavior.)
  async function openFromHistory(summary: PromotionSummary): Promise<void> {
    router.navigate('espacos');
    await promotion.open(summary);
  }
</script>

<AppShell navItems={NAV_ITEMS} route={router.route}>
  {#snippet header({ toggle, open })}
    <header class="topbar">
      <button
        class="topbar__menu"
        type="button"
        aria-label="Abrir menu"
        aria-expanded={open}
        aria-controls="sidebar-nav"
        onclick={toggle}
      >
        <Icon name="menu" />
      </button>
      <h1 class="topbar__title">{SECTION_TITLE[router.route.id]}</h1>
      <div class="topbar__right">
        {#if who?.email}
          <span class="identity"><span class="identity__dot"></span>{who.email}</span>
        {/if}
      </div>
    </header>
  {/snippet}

  {#if router.route.id === 'inicio'}
    <Card title="Início" subtitle="Painel do usuário — visão geral dos seus espaços e promoções.">
      <div class="placeholder">
        <p class="muted">
          O painel inicial chega em breve: seus Genie Spaces e suas promoções recentes em um só lugar,
          com um resumo de como funciona a promoção governada.
        </p>
        <Button onclick={() => router.navigate('espacos')}>Ir para Meus espaços →</Button>
      </div>
    </Card>
  {:else if router.route.id === 'espacos'}
    <MeusEspacos {promotion} userEmail={who?.email ?? null} onGoToNew={() => router.navigate('novo')} />
  {:else if router.route.id === 'promocoes'}
    <MinhasPromocoes {who} onOpen={openFromHistory} />
  {:else}
    <NovoEspaco />
  {/if}
</AppShell>

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
  .placeholder {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-4);
  }
  @media (max-width: 860px) {
    .topbar {
      padding: 0 var(--space-4);
    }
    .topbar__menu {
      display: inline-flex;
    }
    .identity {
      max-width: 9rem;
    }
  }
</style>

<script lang="ts">
  import AppShell from './lib/components/AppShell.svelte';
  import type { NavItem } from './lib/components/Sidebar.svelte';
  import Topbar from './lib/components/Topbar.svelte';
  import Home from './screens/Home.svelte';
  import MeusEspacos from './screens/MeusEspacos.svelte';
  import MinhasPromocoes from './screens/MinhasPromocoes.svelte';
  import NovoEspaco from './screens/NovoEspaco.svelte';
  import { getWhoami, type PromotionSummary } from './lib/api';
  import { Promotion } from './lib/promotion.svelte';
  import { Router } from './lib/router.svelte';
  import type { RouteId } from './lib/route';
  import type { Whoami, PromotableResource } from './lib/types';

  // The shared promotion flow state (selection → review → approval).
  const promotion = new Promotion();

  // Hash router — the URL is the single source of truth for the active screen.
  const router = new Router();

  // OBO identity (drives the header identity/role + the review AI-trust badge + SoD). Display-only.
  let who = $state<Whoami | null>(null);
  getWhoami()
    .then((w) => {
      who = w;
      promotion.viewerEmail = w.email;
      promotion.requesterEmail = w.email;
      promotion.steward = w.steward;
      // Identity-derived role: the viewer IS the Steward when their OBO email matches the configured
      // steward. The approval view follows this + whether the promotion is theirs; the real SoD is
      // still GitHub's gate.
      promotion.isSteward = !!(
        w.email && w.steward && w.email.toLowerCase() === w.steward.toLowerCase()
      );
    })
    .catch(() => {});

  // Recover-on-load (LB3): restore the most-recent promotion from its STORED snapshot — no reviewer
  // re-run. Provides continuity for the "Meus espaços" flow; the home/history surface promotions too.
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

  // Start a promotion for a space and drop into the in-place review flow on "Meus espaços".
  function promoteSpace(resource: PromotableResource): void {
    promotion.select(resource);
    promotion.requestPromotion();
    router.navigate('espacos');
  }

  // Open a promotion's detail via its shareable deep-link (#/promocoes/:id) — the detail view loads
  // the STORED snapshot (no reviewer re-run).
  function openPromotion(summary: PromotionSummary): void {
    router.navigate('promocoes', summary.id);
  }
</script>

<AppShell navItems={NAV_ITEMS} route={router.route}>
  {#snippet header({ toggle, open })}
    <Topbar title={SECTION_TITLE[router.route.id]} {who} {open} onToggle={toggle} />
  {/snippet}

  {#if router.route.id === 'inicio'}
    <Home
      onPromote={promoteSpace}
      onOpenPromotion={openPromotion}
      onGoToNew={() => router.navigate('novo')}
    />
  {:else if router.route.id === 'espacos'}
    <MeusEspacos {promotion} userEmail={who?.email ?? null} onGoToNew={() => router.navigate('novo')} />
  {:else if router.route.id === 'promocoes'}
    <MinhasPromocoes
      {who}
      {promotion}
      onOpen={openPromotion}
      detailId={router.route.param}
      onBack={() => router.navigate('promocoes')}
    />
  {:else}
    <NovoEspaco />
  {/if}
</AppShell>

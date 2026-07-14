<script lang="ts">
  import AppShell from './lib/components/AppShell.svelte';
  import type { NavItem } from './lib/components/Sidebar.svelte';
  import Topbar from './lib/components/Topbar.svelte';
  import Home from './screens/Home.svelte';
  import MeusEspacos from './screens/MeusEspacos.svelte';
  import MinhasPromocoes from './screens/MinhasPromocoes.svelte';
  import AcessoEspacos from './screens/AcessoEspacos.svelte';
  import Rehidratar from './screens/Rehidratar.svelte';
  import Admin from './screens/Admin.svelte';
  import Settings from './screens/Settings.svelte';
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
  // re-run. ONLY on the "Meus espaços" screen, where the review renders in place. On the home the
  // in-flight promotion shows in "Promoções recentes"; on a #/promocoes/:id deep-link openById loads
  // the exact target — running recover there would race it and could surface the WRONG promotion.
  if (router.route.id === 'espacos') promotion.recover().catch(() => {});

  // The admin nav item is only OFFERED when who.is_admin — the real gate is server-side (every F4
  // endpoint 403s a non-admin on the VERIFIED identity regardless), this is purely a UX affordance
  // so a non-admin never sees an entry point to a screen that would just error for them.
  const NAV_ITEMS: NavItem[] = $derived([
    { id: 'inicio', label: 'Início', icon: 'home' },
    { id: 'espacos', label: 'Meus espaços', icon: 'grid' },
    { id: 'promocoes', label: 'Minhas promoções', icon: 'git-branch' },
    { id: 'acesso', label: 'Acesso', icon: 'check-circle' },
    { id: 'rehidratar', label: 'Trazer de volta para o dev', icon: 'download' },
    ...(who?.is_admin ? [{ id: 'admin' as const, label: 'Administração', icon: 'shield' as const }] : []),
    ...(who?.is_admin
      ? [{ id: 'configuracoes' as const, label: 'Configurações', icon: 'settings' as const }]
      : []),
  ]);

  const SECTION_TITLE: Record<RouteId, string> = {
    inicio: 'Início',
    espacos: 'Meus espaços',
    promocoes: 'Minhas promoções',
    acesso: 'Acesso',
    rehidratar: 'Trazer de volta para o dev',
    admin: 'Administração',
    configuracoes: 'Configurações',
  };

  // Choose a space (from Home or "Meus espaços") and land on the confirmation step there (G3):
  // select() only — the panel bound to the chosen space (optional access declaration → "Confirmar
  // promoção") is what actually fires requestPromotion().
  function promoteSpace(resource: PromotableResource): void {
    promotion.select(resource);
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
      promoting={promotion.phase === 'reviewing'}
    />
  {:else if router.route.id === 'espacos'}
    <MeusEspacos
      {promotion}
      userEmail={who?.email ?? null}
      devHost={who?.dev_host ?? null}
      prodHost={who?.prod_host ?? null}
    />
  {:else if router.route.id === 'promocoes'}
    <MinhasPromocoes
      {who}
      {promotion}
      onOpen={openPromotion}
      detailId={router.route.param}
      onBack={() => router.navigate('promocoes')}
    />
  {:else if router.route.id === 'acesso'}
    <AcessoEspacos {who} />
  {:else if router.route.id === 'rehidratar'}
    <Rehidratar devHost={who?.dev_host ?? null} />
  {:else if router.route.id === 'admin'}
    <Admin devHost={who?.dev_host ?? null} />
  {:else if router.route.id === 'configuracoes'}
    <Settings />
  {/if}
</AppShell>

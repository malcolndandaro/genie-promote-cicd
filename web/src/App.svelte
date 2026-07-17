<script lang="ts">
  import AppShell from './lib/components/AppShell.svelte';
  import type { NavSection } from './lib/components/Sidebar.svelte';
  import Topbar from './lib/components/Topbar.svelte';
  import MeusEspacos from './screens/MeusEspacos.svelte';
  import PromotionDetail from './screens/PromotionDetail.svelte';
  import RevisaoPromocoes from './screens/RevisaoPromocoes.svelte';
  import Auditoria from './screens/Auditoria.svelte';
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

  // S7 pilot IA: one author surface, one Steward queue and two Admin surfaces. Promotion detail
  // stays a shareable deep link but never becomes a fifth navigation entry. Server-side gates are
  // still authoritative; this merely prevents retired demo surfaces from being discoverable.
  const NAV_SECTIONS: NavSection[] = $derived([
    {
      title: 'Meu trabalho',
      items: [{ id: 'espacos', label: 'Meus espaços', icon: 'grid' }],
    },
    ...(who?.is_steward
      ? [{
          title: 'Revisão de promoções',
          items: [{ id: 'revisao' as const, label: 'Aguardando minha revisão', icon: 'git-branch' as const }],
        }]
      : []),
    ...(who?.is_admin
      ? [{
          title: 'Administração',
          items: [
            { id: 'configuracoes' as const, label: 'Configurações', icon: 'settings' as const },
            { id: 'auditoria' as const, label: 'Auditoria', icon: 'grid' as const },
          ],
        }]
      : []),
  ]);

  const SECTION_TITLE: Record<RouteId, string> = {
    espacos: 'Meus espaços',
    promocoes: 'Detalhe da promoção',
    revisao: 'Revisão de promoções',
    auditoria: 'Auditoria',
    configuracoes: 'Configurações',
  };

  // Keep history exploration contextual: clicking a Space/run fills the right-hand panel on the
  // same page with its STORED snapshot (no reviewer re-run). Shared #/promocoes/:id links remain
  // supported by PromotionDetail for URLs opened directly.
  function openPromotion(summary: PromotionSummary): void {
    void promotion.open(summary);
  }
</script>

<AppShell sections={NAV_SECTIONS} route={router.route}>
  {#snippet header({ toggle, open })}
    <Topbar title={SECTION_TITLE[router.route.id]} {who} {open} onToggle={toggle} />
  {/snippet}

  {#if router.route.id === 'espacos'}
    <MeusEspacos
      {promotion}
      {who}
      devHost={who?.dev_host ?? null}
      prodHost={who?.prod_host ?? null}
      onOpenPromotion={openPromotion}
    />
  {:else if router.route.id === 'promocoes'}
    <PromotionDetail
      {who}
      {promotion}
      detailId={router.route.param}
      onBack={() => router.navigate('espacos')}
    />
  {:else if router.route.id === 'revisao'}
    <RevisaoPromocoes />
  {:else if router.route.id === 'auditoria'}
    <Auditoria devHost={who?.dev_host ?? null} />
  {:else if router.route.id === 'configuracoes'}
    <Settings />
  {/if}
</AppShell>

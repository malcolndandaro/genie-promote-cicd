<script lang="ts">
  import Header from './lib/components/Header.svelte';
  import Tabs from './lib/components/Tabs.svelte';
  import type { Tab } from './lib/components/Tabs.svelte';
  import MeusEspacos from './screens/MeusEspacos.svelte';
  import MinhasPromocoes from './screens/MinhasPromocoes.svelte';
  import NovoEspaco from './screens/NovoEspaco.svelte';
  import { getWhoami, type PromotionSummary } from './lib/api';
  import { Promotion } from './lib/promotion.svelte';
  import type { Whoami } from './lib/types';

  // The shared promotion flow state (selection → review → approval).
  const promotion = new Promotion();

  // OBO identity (drives the header badge + the review AI-trust badge + SV4's SoD). Display-only,
  // so a failure is silent — the spaces error surfaces any re-auth need. The requester/steward
  // identities feed the separation-of-duties model.
  let who = $state<Whoami | null>(null);
  getWhoami()
    .then((w) => {
      who = w;
      promotion.viewerEmail = w.email;
      promotion.requesterEmail = w.email;
      promotion.steward = w.steward;
      // Identity-derived role (replaces the old manual toggle): the viewer IS the Steward when their
      // OBO email matches the configured steward. The approval view follows this + whether the
      // promotion is theirs; the real SoD is still GitHub's gate.
      promotion.isSteward = !!(
        w.email && w.steward && w.email.toLowerCase() === w.steward.toLowerCase()
      );
    })
    .catch(() => {});

  // Recover-on-load (LB3): restore the most-recent promotion from its STORED snapshot — no reviewer
  // re-run. Best-effort + independent of whoami; the live status resumes via the polling effect.
  promotion.recover().catch(() => {});

  let tab = $state('spaces');
  const TABS: Tab[] = [
    { value: 'spaces', label: 'Meus espaços' },
    { value: 'history', label: 'Minhas promoções' },
    { value: 'new', label: '＋ Novo Genie Space' },
  ];

  // Open a promotion from the history list: load its stored snapshot + audit, then show it in the
  // review view (the "Meus espaços" panel renders the active promotion). No reviewer re-run.
  async function openFromHistory(summary: PromotionSummary): Promise<void> {
    tab = 'spaces'; // switch first so the loading/review state renders in place (open() resets it)
    await promotion.open(summary);
  }
</script>

<Header>
  {#snippet right()}
    {#if who?.email}
      <span class="identity"><span class="identity__dot"></span>{who.email}</span>
    {/if}
  {/snippet}
</Header>

<main class="container page">
  <div class="stack">
    <Tabs tabs={TABS} bind:active={tab} idBase="promote" />
    <div role="tabpanel" id={`promote-panel-${tab}`} aria-labelledby={`promote-tab-${tab}`} tabindex="-1">
      {#if tab === 'spaces'}
        <MeusEspacos {promotion} userEmail={who?.email ?? null} onGoToNew={() => (tab = 'new')} />
      {:else if tab === 'history'}
        <MinhasPromocoes {who} onOpen={openFromHistory} />
      {:else}
        <NovoEspaco />
      {/if}
    </div>
  </div>
</main>

<style>
  .page {
    padding-top: var(--space-6);
    padding-bottom: var(--space-6);
  }
  .identity {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.85rem;
    color: rgba(255, 255, 255, 0.92);
  }
  .identity__dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--accent);
  }
</style>

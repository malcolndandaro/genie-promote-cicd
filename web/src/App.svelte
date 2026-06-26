<script lang="ts">
  import Header from './lib/components/Header.svelte';
  import Tabs from './lib/components/Tabs.svelte';
  import type { Tab } from './lib/components/Tabs.svelte';
  import MeusEspacos from './screens/MeusEspacos.svelte';
  import NovoEspaco from './screens/NovoEspaco.svelte';
  import { getWhoami } from './lib/api';
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
      promotion.requesterEmail = w.email;
      promotion.steward = w.steward;
    })
    .catch(() => {});

  // Recover-on-load (LB3): restore the most-recent promotion from its STORED snapshot — no reviewer
  // re-run. Best-effort + independent of whoami; the live status resumes via the polling effect.
  promotion.recover().catch(() => {});

  let tab = $state('spaces');
  const TABS: Tab[] = [
    { value: 'spaces', label: 'Meus espaços' },
    { value: 'new', label: '＋ Novo Genie Space' },
  ];
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

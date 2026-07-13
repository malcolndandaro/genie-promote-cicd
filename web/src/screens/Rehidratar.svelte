<script lang="ts">
  // G5 — the general-case entry point to prod->dev rehydrate, reachable from the sidebar: unlike
  // RehydrateAction (started FROM a Space's own promotion detail), this screen has no promotion in
  // hand — the user picks ANY prod Space from the live listing (getProdSpaces), never a typed id.
  // The engine resolves which Promotion produced it (for the audit trail) server-side.
  import Card from '../lib/components/Card.svelte';
  import Picker from '../lib/components/Picker.svelte';
  import RehydrateFlow from '../lib/components/RehydrateFlow.svelte';
  import { getProdSpaces } from '../lib/api';
  import type { PickerOption } from '../lib/picker';

  interface Props {
    /** The dev workspace host (`/api/whoami.dev_host`) — threaded down to the flow's success link. */
    devHost: string | null;
  }
  let { devHost }: Props = $props();

  let space = $state<PickerOption | null>(null);

  async function searchProdSpaces(q: string): Promise<PickerOption[]> {
    const spaces = await getProdSpaces(q);
    return spaces.map((s) => ({ id: s.id, label: s.title, sublabel: s.id }));
  }
</script>

<div class="stack">
  <Card
    title="Trazer de volta para o dev"
    subtitle="O dev é um sandbox limpo periodicamente — exporte um espaço de produção de volta para o dev para continuar editando."
  >
    <Picker
      label="Genie Space em produção"
      placeholder="Buscar Genie Space em produção…"
      search={searchProdSpaces}
      bind:value={space}
    />
  </Card>

  {#if space}
    {#key space.id}
      <Card title={space.label} subtitle={space.id}>
        <RehydrateFlow
          sourceProdSpaceId={space.id}
          sourceTitle={space.label}
          {devHost}
          onExit={() => (space = null)}
        />
      </Card>
    {/key}
  {/if}
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
</style>

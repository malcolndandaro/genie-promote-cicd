<script lang="ts">
  // A3/F1 + G5: "Exportar para dev" on a Space's OWN promotion detail — one of the two entry points
  // to the rehydrate flow (the other is the standalone "Trazer de volta para o dev" screen,
  // `Rehidratar.svelte`, reachable from the sidebar for the general case). This one already knows
  // its source Space + Promotion (from the promotion it's rendered under), so the trigger is a
  // single button — no picker needed here.
  import Button from './Button.svelte';
  import RehydrateFlow from './RehydrateFlow.svelte';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    /** The dev workspace host (`/api/whoami.dev_host`) — threaded down so the flow's success state
     * can link to the resulting dev Space. */
    devHost?: string | null;
  }
  let { promotion, devHost = null }: Props = $props();

  // Only meaningful once the Space is actually promoted/live in prod — reseeding dev from a
  // promotion that hasn't deployed would rehydrate a Space that doesn't exist there yet.
  let deployed = $derived(promotion.liveStatus?.phase === 'deployed');
  let open = $state(false);

  function onDone(): void {
    void promotion.refreshAudit(); // the "Rehidratado para dev" event shows up right away
  }
</script>

{#if deployed}
  <div class="rehydrate">
    {#if !open}
      <Button variant="outline" onclick={() => (open = true)}>Exportar para dev</Button>
      <p class="text-xs muted rehydrate__hint">
        Traz este Space de volta para o dev (o ambiente é limpo periodicamente), sem abrir PR.
      </p>
    {:else if promotion.resource}
      <RehydrateFlow
        sourceProdSpaceId={promotion.resource.id}
        sourceTitle={promotion.resource.title}
        promotionId={promotion.promotionId}
        {devHost}
        {onDone}
        onExit={() => (open = false)}
      />
    {/if}
  </div>
{/if}

<style>
  .rehydrate {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
    margin-top: var(--space-4);
    padding-top: var(--space-4);
    border-top: 1px solid var(--border);
    width: 100%;
  }
  .rehydrate__hint {
    margin: 0;
  }
</style>

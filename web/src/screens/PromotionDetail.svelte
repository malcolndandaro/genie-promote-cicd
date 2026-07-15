<script lang="ts">
  // S3: was "Minhas promoções" (list + detail); the list moved into MeusEspacos.svelte (D3's
  // merge). This screen is now JUST the `#/promocoes/:id` deep-link detail view — opening a
  // promotion's STORED snapshot (no reviewer re-run). Still reachable by a shared link even
  // though there's no longer a nav item pointing at it.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import PromotionReview from '../lib/components/PromotionReview.svelte';
  import { untrack } from 'svelte';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { Whoami } from '../lib/types';

  interface Props {
    who: Whoami | null;
    promotion: Promotion;
    /** Route param `:id` — the promotion to open. Absent (bare `#/promocoes`) redirects to
     * "Meus espaços", since there's no standalone list here anymore. */
    detailId?: string;
    /** Return to "Meus espaços" (there's no separate list screen to go "back" to). */
    onBack: () => void;
  }
  let { who, promotion, detailId, onBack }: Props = $props();

  let detailLoading = $state(false);
  $effect(() => {
    const id = detailId;
    if (!id) {
      onBack(); // bare #/promocoes with no id — nothing to show here anymore
      return;
    }
    // untrack the open call: openById's guard READS promotion.promotionId/phase and the call WRITES
    // them — without untrack the effect would track those reads and loop. We only want to re-open
    // when the route's :id changes.
    untrack(() => {
      detailLoading = true;
      promotion.openById(id).finally(() => (detailLoading = false));
    });
  });
</script>

<div class="stack">
  <button class="back" type="button" onclick={onBack}>← Voltar para meus espaços</button>
  {#if detailLoading && promotion.phase === 'idle'}
    <Card>
      <div class="stack">
        <Skeleton height="2.4rem" />
        <Skeleton height="8rem" />
      </div>
    </Card>
  {:else if promotion.phase === 'error'}
    <Card>
      <div class="error-state" role="alert">
        <span class="error">Não foi possível abrir a promoção: {promotion.error}</span>
        <Button variant="outline" onclick={() => { if (detailId) void promotion.openById(detailId); }}>
          Tentar novamente
        </Button>
      </div>
    </Card>
  {:else if promotion.phase === 'idle'}
    <!-- Loaded but the stored promotion has no review snapshot (defensive — every promotion is
         persisted with one). Never leave the detail blank. -->
    <Card>
      <p class="muted text-sm">Esta promoção ainda não tem uma revisão armazenada.</p>
    </Card>
  {:else}
    <PromotionReview
      {promotion}
      userEmail={who?.email ?? null}
      devHost={who?.dev_host ?? null}
      prodHost={who?.prod_host ?? null}
    />
  {/if}
</div>

<style>
  .back {
    align-self: flex-start;
    appearance: none;
    border: none;
    background: transparent;
    color: var(--accent-hover);
    font-family: inherit;
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    padding: 0;
  }
  .back:hover {
    text-decoration: underline;
  }
  .back:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: var(--radius-sm);
  }
  .error-state {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

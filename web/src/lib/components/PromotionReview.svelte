<script lang="ts">
  import Card from './Card.svelte';
  import Button from './Button.svelte';
  import Badge from './Badge.svelte';
  import Pipeline from './Pipeline.svelte';
  import ReviewPanel from './ReviewPanel.svelte';
  import ApprovalSection from './ApprovalSection.svelte';
  import AuditTrail from './AuditTrail.svelte';
  import { pendingTimeline } from '../pipeline';
  import { PHASE_LABEL, phaseTone } from '../status';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    userEmail: string | null;
  }
  let { promotion, userEmail }: Props = $props();

  // Poll the live PR/CI/deploy status while a promotion PR is open (GH3). The effect lives WITH the
  // review so polling runs exactly when the review is on screen (the active flow OR a promotion
  // detail) and stops when it isn't. It also refreshes the audit trail (the poll reconciles
  // server-side, LB4). Stops at a terminal phase.
  const TERMINAL = new Set(['deployed', 'closed', 'deploy_failed']);
  $effect(() => {
    if (!promotion.pr) return;
    promotion.refreshStatus();
    promotion.refreshAudit();
    const id = setInterval(async () => {
      await promotion.refreshStatus();
      await promotion.refreshAudit();
      if (promotion.liveStatus && TERMINAL.has(promotion.liveStatus.phase)) clearInterval(id);
    }, 5000);
    return () => clearInterval(id);
  });
</script>

{#if promotion.phase !== 'idle'}
  <Card>
    {#if promotion.phase === 'reviewing'}
      <div class="review-running">
        <h3 class="running-heading">
          <span class="running-dot" aria-hidden="true"></span>
          Executando pipeline de promoção…
        </h3>
        <Pipeline steps={pendingTimeline()} running />
      </div>
    {:else if promotion.phase === 'error'}
      <div class="error-state" role="alert">
        <span class="error">Não foi possível solicitar a promoção: {promotion.error}</span>
        <Button variant="outline" onclick={() => promotion.requestPromotion()}>Tentar novamente</Button>
      </div>
    {:else if promotion.phase === 'reviewed' && promotion.review}
      {#if promotion.pr}
        <div class="pr-banner" role="status">
          <span class="pr-banner__dot" aria-hidden="true"></span>
          <span>PR de promoção aberto: <strong>#{promotion.pr.number}</strong></span>
          {#if promotion.liveStatus}
            <Badge tone={phaseTone(promotion.liveStatus.phase)}>
              {PHASE_LABEL[promotion.liveStatus.phase]}
            </Badge>
          {/if}
          <a class="pr-banner__link" href={promotion.pr.url} target="_blank" rel="noopener noreferrer">
            Ver no GitHub ↗
          </a>
        </div>
      {/if}
      <ReviewPanel review={promotion.review} {userEmail} liveStatus={promotion.liveStatus}>
        {#snippet approval()}
          <ApprovalSection {promotion} />
        {/snippet}
      </ReviewPanel>
      {#if promotion.promotionId}
        <div class="audit-section">
          <AuditTrail events={promotion.audit} />
        </div>
      {/if}
    {/if}
  </Card>
{/if}

<style>
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
  .audit-section {
    margin-top: var(--space-5);
    padding-top: var(--space-5);
    border-top: 1px solid var(--border);
  }
  .pr-banner {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-bottom: var(--space-5);
    padding: var(--space-3) var(--space-4);
    background: var(--accent-soft);
    border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
    border-radius: var(--radius-sm);
    font-size: 0.9rem;
  }
  .pr-banner__dot {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 50%;
    background: var(--accent);
    flex-shrink: 0;
  }
  .pr-banner__link {
    margin-left: auto;
    font-weight: 600;
    color: var(--accent-hover);
    white-space: nowrap;
  }
  .running-heading {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.95rem;
    margin-bottom: var(--space-4);
  }
  .running-dot {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 50%;
    background: var(--accent);
    animation: running-dot 1.2s ease-in-out infinite;
  }
  @keyframes running-dot {
    0%,
    100% {
      opacity: 0.35;
      transform: scale(0.8);
    }
    50% {
      opacity: 1;
      transform: scale(1.1);
    }
  }
</style>

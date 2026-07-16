<script lang="ts">
  import Card from './Card.svelte';
  import Button from './Button.svelte';
  import Badge from './Badge.svelte';
  import Pipeline from './Pipeline.svelte';
  import ReviewPanel from './ReviewPanel.svelte';
  import AuditTrail from './AuditTrail.svelte';
  import RehydrateAction from './RehydrateAction.svelte';
  import DriftPanel from './DriftPanel.svelte';
  import { pendingTimeline } from '../pipeline';
  import { PHASE_LABEL, phaseTone } from '../status';
  import { getPromotionDrift } from '../api';
  import { genieSpaceUrl } from '../links';
  import type { DriftReport } from '../types';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    userEmail: string | null;
    /** The dev workspace host (G5, `/api/whoami.dev_host`) — threaded down to RehydrateAction. */
    devHost?: string | null;
    /** W3: the prod workspace host (`/api/whoami.prod_host`) — used to build the "Abrir Genie em
     * produção" deep-link once the promotion is deployed. */
    prodHost?: string | null;
  }
  let { promotion, userEmail, devHost = null, prodHost = null }: Props = $props();

  // W3: only once BOTH the resolved prod Space id (server-side, phase-gated — see
  // `_with_prod_space_id`) AND the prod host are available — never guess a URL from a partial
  // answer.
  let prodGenieUrl = $derived(
    promotion.liveStatus?.phase === 'deployed' && promotion.liveStatus?.prod_space_id && prodHost
      ? genieSpaceUrl(prodHost, promotion.liveStatus.prod_space_id)
      : null,
  );

  // UI cleanup: the Steward's approve deep-link, relocated from the removed ApprovalSection into
  // the PR banner. Only THIS viewer's approval affordance (promotion.svelte.ts's `approval` getter
  // — never the author, never while a BLOCKER stands) AND only once the GitHub gate is actually
  // waiting (never guess a run_url from a partial status).
  let approveUrl = $derived(
    promotion.approval.canApprove && promotion.liveStatus?.phase === 'awaiting_approval'
      ? promotion.liveStatus?.deploy?.run_url ?? null
      : null,
  );

  // F5 US-35: contextual drift, surfaced HERE for the assigned Steward — not only on a passive
  // Settings panel nobody opens. Fetched once per promotion (not polled — role/GitHub-gate drift
  // doesn't change on the promotion's own 5s status cadence); failures are swallowed to `null` so a
  // drift-check hiccup never blocks rendering the review itself.
  let driftP = $state<Promise<DriftReport> | null>(null);
  $effect(() => {
    const id = promotion.promotionId;
    driftP = promotion.isSteward && id ? getPromotionDrift(id) : null;
  });

  // Poll the live PR/CI/deploy status while a promotion PR is open (GH3). The effect lives WITH the
  // review so polling runs exactly when the review is on screen (the active flow OR a promotion
  // detail) and stops when it isn't. It also refreshes the audit trail (the poll reconciles
  // server-side, LB4). Stops at a terminal phase.
  // A failed deploy is recoverable forward by KIP. Keep polling so a provider re-run (new Attempt,
  // same approved revisions) becomes the `resuming` state without requiring a reload.
  const TERMINAL = new Set(['deployed', 'closed']);
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
      {#if promotion.noChange}
        <div class="no-change-banner" role="status">
          <span class="no-change-banner__title">Nada a promover</span>
          <span class="no-change-banner__body">
            Este Genie Space já está em produção com conteúdo idêntico — não há mudanças para promover,
            então nenhum PR foi aberto. Edite o espaço no dev e solicite a promoção novamente para
            gerar um diff. (A revisão abaixo rodou mesmo assim, para referência.)
          </span>
        </div>
      {/if}
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
          {#if approveUrl}
            <a class="pr-banner__approve" href={approveUrl} target="_blank" rel="noopener noreferrer">
              Aprovar no GitHub ↗
            </a>
          {/if}
          {#if prodGenieUrl}
            <a class="pr-banner__link" href={prodGenieUrl} target="_blank" rel="noopener noreferrer">
              Abrir Genie em produção ↗
            </a>
          {/if}
        </div>
      {/if}
      <ReviewPanel
        review={promotion.review}
        {userEmail}
        liveStatus={promotion.liveStatus}
        {devHost}
        devSpaceId={promotion.resource?.id ?? null}
        showPipeline={!!promotion.pr && !promotion.noChange}
      >
        {#snippet approval()}
          {#if driftP}
            <div class="drift-section">
              {#await driftP then report}
                {#if report.findings.length > 0}
                  <div class="drift-callout" role="status">
                    <span class="drift-callout__heading">
                      Antes de aprovar: divergência entre o papel de Steward e o gate do GitHub
                    </span>
                    <DriftPanel {report} compact />
                  </div>
                {/if}
              {:catch}
                <!-- a drift-check failure must never block rendering the review/approval itself -->
              {/await}
            </div>
          {/if}
        {/snippet}
      </ReviewPanel>
      {#if promotion.promotionId}
        <div class="audit-section">
          <AuditTrail events={promotion.audit} />
        </div>
      {/if}
      <RehydrateAction {promotion} {devHost} />
    {/if}
  </Card>
{/if}

<style>
  .drift-section {
    margin-bottom: var(--space-4);
  }
  .drift-callout {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    background: var(--warning-soft);
    border: 1px solid color-mix(in srgb, var(--warning) 30%, transparent);
    border-radius: var(--radius-sm);
  }
  .drift-callout__heading {
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--warning);
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
  .audit-section {
    margin-top: var(--space-5);
    padding-top: var(--space-5);
    border-top: 1px solid var(--border);
  }
  .no-change-banner {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    margin-bottom: var(--space-5);
    padding: var(--space-3) var(--space-4);
    background: var(--warning-soft);
    border: 1px solid color-mix(in srgb, var(--warning) 30%, transparent);
    border-radius: var(--radius-sm);
    font-size: 0.9rem;
  }
  .no-change-banner__title {
    font-weight: 600;
  }
  .no-change-banner__body {
    color: var(--muted-foreground);
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
  .pr-banner__approve {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.3rem 0.85rem;
    border-radius: var(--radius-pill);
    background: var(--accent);
    color: var(--accent-foreground);
    font-size: 0.85rem;
    font-weight: 600;
    text-decoration: none;
    white-space: nowrap;
    transition: background-color 0.15s ease;
  }
  .pr-banner__approve:hover {
    background: var(--accent-hover);
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

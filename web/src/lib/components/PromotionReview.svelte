<script lang="ts">
  import { untrack } from 'svelte';
  import Card from './Card.svelte';
  import Button from './Button.svelte';
  import Badge from './Badge.svelte';
  import Pipeline from './Pipeline.svelte';
  import ReviewPanel from './ReviewPanel.svelte';
  import AuditTrail from './AuditTrail.svelte';
  import RehydrateAction from './RehydrateAction.svelte';
  import { pendingTimeline } from '../pipeline';
  import { PHASE_LABEL, phaseTone } from '../status';
  import { genieSpaceUrl } from '../links';
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

  // Poll the live PR/CI/deploy status while a promotion PR is open (GH3). The effect lives WITH the
  // review so polling runs exactly when the review is on screen (the active flow OR a promotion
  // detail) and stops when it isn't. It also refreshes the audit trail (the poll reconciles
  // server-side, LB4). Stops at a terminal phase.
  // A failed deploy is recoverable forward by KIP. Keep polling so a provider re-run (new Attempt,
  // same approved revisions) becomes the `resuming` state without requiring a reload.
  const TERMINAL = new Set(['deployed', 'closed']);
  $effect(() => {
    if (!promotion.pr) return;
    // The methods mutate their own loading/result state. Keep those internal reads out of this
    // effect's dependency graph or each mutation would immediately restart the poll loop.
    untrack(() => {
      void promotion.refreshStatus();
      void promotion.refreshAudit();
    });
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
          <span>Rascunho pronto: <strong>#{promotion.pr.number}</strong></span>
          {#if promotion.waitingForLiveStatus}
            <Badge tone="neutral">Atualizando status…</Badge>
          {:else if promotion.liveStatus}
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
        {#if !promotion.liveStatus || (promotion.liveStatus.phase !== 'deployed' && promotion.liveStatus.phase !== 'merged' && promotion.liveStatus.phase !== 'deploying' && promotion.liveStatus.phase !== 'awaiting_approval')}
          <p class="handoff-hint" role="note">
            Rascunho pronto — o Responsável Técnico revisa e promove no GitHub.
          </p>
        {/if}
      {/if}
      {#if promotion.waitingForLiveStatus}
        <div class="status-sync" role="status" aria-label="Atualizando status no GitHub">
          {#if promotion.statusError}
            <div>
              <strong>Não foi possível confirmar o status atual.</strong>
              <p>O último estado salvo não será tratado como atual até o GitHub responder.</p>
              <p class="status-sync__review">Resultado da revisão: {promotion.review.gate.summary}</p>
            </div>
            <Button variant="outline" onclick={() => promotion.refreshStatus()}>Tentar novamente</Button>
          {:else}
            <span class="status-sync__spinner" aria-hidden="true"></span>
            <div>
              <strong>Atualizando status no GitHub…</strong>
              <p>Confirmando a etapa atual antes de exibir a decisão vigente.</p>
              <p class="status-sync__review">Resultado da revisão: {promotion.review.gate.summary}</p>
            </div>
          {/if}
        </div>
      {/if}
      <ReviewPanel
        review={promotion.review}
        {userEmail}
        liveStatus={promotion.statusFresh ? promotion.liveStatus : null}
        statusPending={promotion.waitingForLiveStatus}
        {devHost}
        devSpaceId={promotion.resource?.id ?? null}
        showPipeline={!!promotion.pr && !promotion.noChange}
        evidenceLoading={promotion.evidenceLoading}
        evidenceError={promotion.evidenceError}
        onLoadEvidence={() => promotion.refreshDeploymentEvidence()}
      />
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
  .status-sync {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: 8rem;
    margin-bottom: var(--space-5);
    padding: var(--space-5);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface-inset);
  }
  .status-sync p {
    margin: var(--space-1) 0 0;
    color: var(--muted-foreground);
    font-size: 0.8rem;
  }
  .status-sync .status-sync__review { color: var(--foreground); font-weight: 650; }
  .status-sync__spinner {
    width: 1.35rem;
    height: 1.35rem;
    flex: 0 0 auto;
    border: 3px solid color-mix(in srgb, var(--accent) 24%, var(--border));
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: status-spin 0.7s linear infinite;
  }
  @keyframes status-spin { to { transform: rotate(360deg); } }
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
  .handoff-hint {
    margin: calc(var(--space-3) * -1) 0 var(--space-5);
    padding: var(--space-2) var(--space-4);
    border-radius: 0 0 var(--radius-sm) var(--radius-sm);
    background: color-mix(in srgb, var(--accent-soft) 70%, transparent);
    color: var(--muted-foreground);
    font-size: 0.8rem;
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

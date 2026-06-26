<script lang="ts">
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import Select from '../lib/components/Select.svelte';
  import Pipeline from '../lib/components/Pipeline.svelte';
  import ReviewPanel from '../lib/components/ReviewPanel.svelte';
  import ApprovalSection from '../lib/components/ApprovalSection.svelte';
  import AuditTrail from '../lib/components/AuditTrail.svelte';
  import { getResources, isAuthError } from '../lib/api';
  import { kindMeta } from '../lib/resources';
  import { pendingTimeline } from '../lib/pipeline';
  import { PHASE_LABEL, phaseTone } from '../lib/status';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { PromotableResource } from '../lib/types';

  interface Props {
    promotion: Promotion;
    userEmail: string | null;
    onGoToNew?: () => void;
  }
  let { promotion, userEmail, onGoToNew }: Props = $props();

  // Poll the live PR/CI/deploy status while a promotion PR is open (GH3). The effect depends on
  // `promotion.pr`, so it (re)starts when a PR opens and stops/cleans up when it changes or
  // unmounts. Polling stops once the promotion reaches a terminal phase (no point hitting GitHub).
  const TERMINAL = new Set(['deployed', 'closed', 'deploy_failed']);
  $effect(() => {
    if (!promotion.pr) return;
    // The status poll reconciles server-side (LB4) — so refresh the audit trail alongside it to
    // surface newly-recorded events. Run both now + every 5s until a terminal phase.
    promotion.refreshStatus();
    promotion.refreshAudit();
    const id = setInterval(async () => {
      await promotion.refreshStatus();
      await promotion.refreshAudit();
      if (promotion.liveStatus && TERMINAL.has(promotion.liveStatus.phase)) clearInterval(id);
    }, 5000);
    return () => clearInterval(id);
  });

  // The user's promotable resources (OBO). In $state so an error is retryable.
  let resourcesP = $state(getResources());

  // `promotion` is the single source of truth for the selection — no parallel local state.
  const reload = () => {
    resourcesP = getResources();
    promotion.select(null); // a fresh list invalidates any prior (possibly gone) selection
  };

  function onSelect(id: string, resources: PromotableResource[]) {
    promotion.select(resources.find((r) => r.id === id) ?? null);
  }
</script>

<Card title="Meus espaços" subtitle="Selecione um recurso e solicite a revisão de promoção.">
  {#await resourcesP}
    <div class="stack">
      <Skeleton height="2.6rem" />
      <Skeleton height="2.6rem" width="60%" />
    </div>
  {:then resources}
    {#if resources.length === 0}
      <div class="empty">
        <p class="empty__title">Nenhum Genie Space encontrado</p>
        <p class="muted text-sm">
          Crie um para começar a promover — a autoria rica acontece no Genie nativo.
        </p>
        {#if onGoToNew}
          <Button variant="outline" onclick={onGoToNew}>＋ Novo Genie Space</Button>
        {/if}
      </div>
    {:else}
      <div class="stack">
        <div class="field">
          <label class="field__label" for="meus-espacos-select">Recurso</label>
          <Select
            id="meus-espacos-select"
            options={resources.map((r) => ({ value: r.id, label: r.title }))}
            value={promotion.selectedId}
            onchange={(id) => onSelect(id, resources)}
            placeholder="Selecione um espaço"
            disabled={promotion.phase === 'reviewing'}
          />
        </div>

        {#if promotion.resource}
          <p class="selected-note text-sm">
            <Badge tone="accent">{kindMeta(promotion.resource.kind).label}</Badge>
            <span>{promotion.resource.title}</span>
          </p>
        {/if}

        <div>
          <Button
            onclick={() => promotion.requestPromotion()}
            disabled={!promotion.resource}
            loading={promotion.phase === 'reviewing'}
          >
            {promotion.phase === 'reviewing' ? 'Solicitando…' : 'Solicitar promoção →'}
          </Button>
        </div>
      </div>
    {/if}
  {:catch err}
    <div class="error-state" role="alert">
      <span class="error">
        {#if isAuthError(err)}
          Sessão expirada — recarregue a página para reautenticar.
        {:else}
          Não foi possível listar os espaços: {err instanceof Error ? err.message : String(err)}
        {/if}
      </span>
      {#if isAuthError(err)}
        <Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
      {:else}
        <Button variant="outline" onclick={reload}>Tentar novamente</Button>
      {/if}
    </div>
  {/await}

  <!-- The review result: an animated pipeline while in flight, then the full review panel. -->
  {#if promotion.phase === 'reviewing'}
    <div class="review-running">
      <h3 class="running-heading">
        <span class="running-dot" aria-hidden="true"></span>
        Executando pipeline de promoção…
      </h3>
      <Pipeline steps={pendingTimeline()} running />
    </div>
  {:else if promotion.phase === 'error'}
    <div class="review-stub error-state" role="alert">
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

<style>
  .field {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .field__label {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted-foreground);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .selected-note {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin: 0;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4) 0;
  }
  .empty__title {
    margin: 0;
    font-weight: 600;
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
  .review-stub {
    margin-top: var(--space-5);
    padding-top: var(--space-4);
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .review-running {
    margin-top: var(--space-5);
    padding-top: var(--space-5);
    border-top: 1px solid var(--border);
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
    margin-top: var(--space-5);
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

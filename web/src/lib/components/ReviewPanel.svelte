<script lang="ts">
  import { fly } from 'svelte/transition';
  import Badge from './Badge.svelte';
  import Pipeline from './Pipeline.svelte';
  import EvalResultsPanel from './EvalResultsPanel.svelte';
  import Markdown from './Markdown.svelte';
  import DecisionSummary from './DecisionSummary.svelte';
  import { severityTone, buildPromotionSteps } from '../pipeline';
  import type { Review } from '../types';

  interface Props {
    review: Review;
    userEmail: string | null;
    /** Live PR/CI/deploy status (GH3/GH5) — drives the git steps (pr_review/merge/approval/deploy)
     * of the pipeline. Null until the first poll lands; reflects GitHub, never asserts. */
    liveStatus?: import('../api').PromoteStatus | null;
    /** W3: the dev workspace host + the promotion's dev Space id — threaded down to
     * EvalResultsPanel's "Abrir benchmarks no dev" deep-link. */
    devHost?: string | null;
    devSpaceId?: string | null;
    /** Optional supplementary content rendered below the review (currently: the Steward's F5
     * drift callout — the approval action itself lives in the PR banner, see PromotionReview). */
    approval?: import('svelte').Snippet;
    /** Whether to render the 7-step promotion pipeline. False on a no-op promotion (no PR opened),
     * where a pipeline would be misleading — the findings still render for reference. Default true. */
    showPipeline?: boolean;
    evidenceLoading?: boolean;
    evidenceError?: string | null;
    onLoadEvidence?: () => void | Promise<void>;
    statusPending?: boolean;
  }
  let {
    review, userEmail, liveStatus = null, devHost = null, devSpaceId = null, approval,
    showPipeline = true, evidenceLoading = false, evidenceError = null, onLoadEvidence,
    statusPending = false,
  }: Props = $props();

  // Findings have no stable id; the list never reorders, so a rule_id+index key is unique & safe.
  let keyed = $derived(review.findings.map((f, i) => ({ ...f, _k: `${f.rule_id}-${i}` })));
  let blockers = $derived(keyed.filter((f) => f.severity === 'BLOCKER' || f.severity === 'OPERATIONAL'));
  let advisories = $derived(keyed.filter((f) => f.severity !== 'BLOCKER' && f.severity !== 'OPERATIONAL'));
  // The 7-step pipeline: verdict steps from review.timeline, git steps driven live by liveStatus.
  let timeline = $derived(buildPromotionSteps(review, liveStatus));
  // Required Público do Space shown so the Steward approves the exact CAN_RUN audience.
  let audienceSpec = $derived(review.audience_spec);
</script>

<div class="review">
  {#if !statusPending}
    <DecisionSummary {review} {liveStatus} mode="decision" />
  {/if}

  <!-- Decision-first findings: blockers are never hidden; guidance is available but collapsed. -->
  <section>
    <h3 class="review__heading">Bloqueios que exigem ação</h3>
    {#if keyed.length === 0}
      <p class="muted text-sm">Nenhum achado — espaço limpo.</p>
    {:else if blockers.length > 0}
      <div class="findings">
        {#each blockers as f, i (f._k)}
          <article class="finding" in:fly={{ y: 10, duration: 260, delay: i * 70 }}>
            <div class="finding__head">
              <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
              <span class="finding__rule">{f.rule_id}</span>
            </div>
            <!-- KA advisory findings return markdown (bold, inline code, lists); render it safely.
                 Plain reviewer findings are single-line text, shown as-is. -->
            {#if f.rule_id.startsWith('KA:')}
              <div class="finding__msg"><Markdown text={f.message} /></div>
            {:else}
              <p class="finding__msg">{f.message}</p>
            {/if}
            {#if f.suggestion}<p class="finding__suggestion">↳ {f.suggestion}</p>{/if}
            {#if f.citation}<p class="finding__citation">{f.citation}</p>{/if}
          </article>
        {/each}
      </div>
    {:else}
      <p class="muted text-sm">Nenhum bloqueio. As orientações opcionais estão recolhidas abaixo.</p>
    {/if}
    {#if advisories.length > 0}
      <details class="advisories">
        <summary>Orientações não bloqueantes ({advisories.length})</summary>
        <div class="findings">
          {#each advisories as f, i (f._k)}
            <article class="finding" in:fly={{ y: 10, duration: 200, delay: i * 40 }}>
              <div class="finding__head">
                <Badge tone={severityTone(f.severity)}>{f.severity}</Badge>
                <span class="finding__rule">{f.rule_id}</span>
              </div>
              {#if f.rule_id.startsWith('KA:')}
                <div class="finding__msg"><Markdown text={f.message} /></div>
              {:else}<p class="finding__msg">{f.message}</p>{/if}
              {#if f.suggestion}<p class="finding__suggestion">↳ {f.suggestion}</p>{/if}
              {#if f.citation}<p class="finding__citation">{f.citation}</p>{/if}
            </article>
          {/each}
        </div>
      </details>
    {/if}
  </section>

  {#if showPipeline}
    <section>
      <h3 class="review__heading">Pipeline de promoção</h3>
      <Pipeline steps={timeline} />
    </section>
  {/if}

  {#if !statusPending}
    <DecisionSummary
      {review}
      {liveStatus}
      mode="evidence"
      {evidenceLoading}
      {evidenceError}
      {onLoadEvidence}
    />
  {/if}

  <!-- Read-only here; reconciliation is governed by CI and never applied from this screen. -->
  {#if audienceSpec && audienceSpec.principals.length > 0}
    <section>
      <h3 class="review__heading">Público do Space</h3>
      <div class="access-chips">
        {#each audienceSpec.principals as principal (principal.principal)}
          <Badge tone="accent">
            {principal.principal}{principal.is_group ? ' (grupo)' : ''} · CAN_RUN
          </Badge>
        {/each}
      </div>
      <p class="muted text-xs">
        O pipeline governa apenas o ACL do Space. Acesso aos dados continua no Terraform da CERC.
      </p>
    </section>
  {/if}

  <EvalResultsPanel evalResult={review.eval} {devHost} {devSpaceId} />

  {#if approval}
    <div class="approval">{@render approval()}</div>
  {/if}
</div>

<style>
  .review {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    margin-top: var(--space-5);
    padding-top: var(--space-5);
    border-top: 1px solid var(--border);
  }
  .review__heading {
    font-size: 0.95rem;
    margin-bottom: var(--space-3);
  }
  .access-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .advisories { margin-top: var(--space-4); }
  .advisories summary { cursor: pointer; color: var(--accent-hover); font-size: 0.82rem; font-weight: 700; }
  .advisories[open] summary { margin-bottom: var(--space-3); }
  .findings {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .finding {
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: var(--space-3) var(--space-4);
    background: var(--surface);
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .finding__head {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .finding__rule {
    font-weight: 600;
    font-size: 0.85rem;
  }
  .finding__msg {
    margin: 0;
    font-size: 0.9rem;
  }
  .finding__suggestion {
    margin: 0;
    font-size: 0.875rem;
    color: var(--muted-foreground);
  }
  .finding__citation {
    margin: 0;
    font-size: 0.75rem;
    font-style: italic;
    color: var(--muted-foreground);
  }
</style>

<script lang="ts">
  import { fly } from 'svelte/transition';
  import Badge from './Badge.svelte';
  import Pipeline from './Pipeline.svelte';
  import EvalResultsPanel from './EvalResultsPanel.svelte';
  import Markdown from './Markdown.svelte';
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
  }
  let {
    review, userEmail, liveStatus = null, devHost = null, devSpaceId = null, approval,
    showPipeline = true,
  }: Props = $props();

  let failed = $derived(review.gate.conclusion === 'failure');
  // Findings have no stable id; the list never reorders, so a rule_id+index key is unique & safe.
  let keyed = $derived(review.findings.map((f, i) => ({ ...f, _k: `${f.rule_id}-${i}` })));
  // The 7-step pipeline: verdict steps from review.timeline, git steps driven live by liveStatus.
  let timeline = $derived(buildPromotionSteps(review, liveStatus));
  // F2: the declared AccessSpec (both permission systems), shown so the Steward approves it as
  // part of the promotion. `access_spec` is optional (a pre-F2 cached/recovered review has none).
  let accessSpec = $derived(review.access_spec);
  let hasDeclaredAccess = $derived(
    !!accessSpec && (accessSpec.space_permissions.length > 0 || accessSpec.uc_principals.length > 0)
  );
</script>

<div class="review">
  <!-- Pipeline timeline (real per-step verdicts from the server; advances on approval). Hidden on a
       no-op promotion (no PR), where the git steps would be a misleading string of pending nodes. -->
  {#if showPipeline}
    <section>
      <h3 class="review__heading">Pipeline de promoção</h3>
      <Pipeline steps={timeline} />
    </section>
  {/if}

  <!-- Gate summary (role so the verdict isn't conveyed by colour alone; failure = alert). -->
  {#if failed}
    <div class="gate gate--fail" role="alert">{review.gate.summary}</div>
  {:else}
    <p
      class="gate-text"
      role="status"
      data-tone={review.gate.conclusion === 'success' ? 'success' : 'warning'}
    >
      {review.gate.summary}
    </p>
  {/if}

  <!-- Findings. -->
  <section>
    <h3 class="review__heading">Achados do Genie Reviewer</h3>
    {#if keyed.length === 0}
      <p class="muted text-sm">Nenhum achado — espaço limpo.</p>
    {:else}
      <div class="findings">
        {#each keyed as f, i (f._k)}
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
    {/if}
  </section>

  <!-- F2: declared access — shown so the Steward approves it as part of the promotion. Read-only
       here (declaration happens on the request form); enforcement is governed (CI, prod SP), never
       applied from this screen. -->
  {#if hasDeclaredAccess && accessSpec}
    <section>
      <h3 class="review__heading">Acesso declarado</h3>
      {#if accessSpec.space_permissions.length > 0}
        <div class="access-group">
          <p class="access-group__label">Permissões do Espaço (Genie)</p>
          <div class="access-chips">
            {#each accessSpec.space_permissions as sp (sp.principal + sp.level)}
              <Badge tone="accent">{sp.principal}{sp.is_group ? ' (grupo)' : ''} · {sp.level}</Badge>
            {/each}
          </div>
        </div>
      {/if}
      {#if accessSpec.uc_principals.length > 0}
        <div class="access-group">
          <p class="access-group__label">Grants de dados (UC SELECT)</p>
          <div class="access-chips">
            {#each accessSpec.uc_principals as p (p.principal)}
              <Badge tone="neutral">{p.principal}{p.is_group ? ' (grupo)' : ''}</Badge>
            {/each}
          </div>
        </div>
      {/if}
      <p class="muted text-xs">
        Aplicado via um grupo dedicado ao espaço, pelo pipeline governado (CI) após a aprovação —
        nunca diretamente pelo app.
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
  .access-group {
    margin-bottom: var(--space-3);
  }
  .access-group__label {
    margin: 0 0 var(--space-2);
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .access-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .gate {
    border-radius: var(--radius-sm);
    padding: var(--space-3) var(--space-4);
    font-weight: 500;
    font-size: 0.9rem;
  }
  .gate--fail {
    background: var(--destructive-soft);
    border: 1px solid color-mix(in srgb, var(--destructive) 30%, transparent);
    color: var(--destructive);
  }
  .gate-text {
    margin: 0;
    font-weight: 600;
    font-size: 0.9rem;
  }
  .gate-text[data-tone='success'] {
    color: var(--success);
  }
  .gate-text[data-tone='warning'] {
    color: var(--warning);
  }
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

<script lang="ts">
  import { fly } from 'svelte/transition';
  import Badge from './Badge.svelte';
  import Pipeline from './Pipeline.svelte';
  import { severityTone } from '../pipeline';
  import type { Review } from '../types';

  interface Props {
    review: Review;
    userEmail: string | null;
    /** Optional steward-approval section rendered below the review (wired in SV4). */
    approval?: import('svelte').Snippet;
  }
  let { review, userEmail, approval }: Props = $props();

  let failed = $derived(review.gate.conclusion === 'failure');
  // Findings have no stable id; the list never reorders, so a rule_id+index key is unique & safe.
  let keyed = $derived(review.findings.map((f, i) => ({ ...f, _k: `${f.rule_id}-${i}` })));
</script>

<div class="review">
  <!-- AI-trust: who it ran as + that findings are AI-generated. -->
  <div class="ai-trust">
    <div class="ai-trust__row">
      <Badge tone="accent">{userEmail ?? 'usuário autenticado'}</Badge>
      <span class="muted text-sm">Achados gerados por IA — verifique antes de aprovar.</span>
    </div>
    <p class="muted text-xs">
      Leitura dos espaços executa como você (OBO); o revisor (LLM) e a checagem de grants executam
      como o service principal do app.
    </p>
  </div>

  <!-- Pipeline timeline (real per-step verdicts from the server). -->
  <section>
    <h3 class="review__heading">Pipeline de promoção</h3>
    <Pipeline steps={review.timeline} />
  </section>

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
            <p class="finding__msg">{f.message}</p>
            {#if f.suggestion}<p class="finding__suggestion">↳ {f.suggestion}</p>{/if}
            {#if f.citation}<p class="finding__citation">{f.citation}</p>{/if}
          </article>
        {/each}
      </div>
    {/if}
  </section>

  <p class="muted text-xs">eval-run: {review.eval.summary}</p>

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
  .ai-trust {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-4);
    background: var(--accent-soft);
    border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
    border-radius: var(--radius-sm);
  }
  .ai-trust__row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .review__heading {
    font-size: 0.95rem;
    margin-bottom: var(--space-3);
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

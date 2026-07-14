<script lang="ts">
  // W3: the "Eval-run" pipeline step used to be a bare status + a one-line summary at the bottom
  // of the review — a business user had no idea WHAT eval-run means or WHICH question failed.
  // This panel is the single source for that explanation now (replaces ReviewPanel's old bottom
  // line), mirroring the `<details>` disclosure idiom Pipeline.svelte/DriftPanel already use.
  import { EVAL_QUESTION_GLYPH, EVAL_QUESTION_LABEL } from '../pipeline';
  import { genieSpaceUrl } from '../links';
  import type { EvalResult } from '../types';

  interface Props {
    evalResult: EvalResult;
    /** The dev workspace host (`/api/whoami.dev_host`) + the promotion's dev Space id — together
     * build the "Abrir benchmarks no dev" deep-link. Either missing hides the link (never guess a
     * URL from a partial answer). */
    devHost?: string | null;
    devSpaceId?: string | null;
  }
  let { evalResult, devHost = null, devSpaceId = null }: Props = $props();

  let n = $derived(evalResult.n ?? 0);
  // Already sorted failures-first by the backend (_decide_eval_with_questions) — rendered as-is.
  let questions = $derived(evalResult.questions ?? []);
  let devUrl = $derived(devHost && devSpaceId ? genieSpaceUrl(devHost, devSpaceId) : null);
</script>

<details class="eval-panel">
  <summary>Ver resultados do eval ({n})</summary>
  <div class="eval-panel__body">
    <p class="eval-panel__explainer">
      Teste automático: o espaço responde corretamente às próprias perguntas de benchmark?
    </p>
    <p class="eval-panel__rate">{evalResult.summary}</p>

    {#if questions.length > 0}
      <ul class="eval-panel__list">
        {#each questions as q, i ((q.question ?? '') + i)}
          <li class="eval-panel__item" data-status={q.status}>
            <span class="eval-panel__glyph" aria-hidden="true">{EVAL_QUESTION_GLYPH[q.status]}</span>
            <span class="eval-panel__question" title={q.question ?? ''}>
              {q.question ?? '(pergunta sem texto)'}
            </span>
            <!-- Status in words: color/glyph must not be the only signal (WCAG 1.4.1). -->
            <span class="visually-hidden">— {EVAL_QUESTION_LABEL[q.status]}</span>
          </li>
        {/each}
      </ul>
    {/if}

    {#if devUrl}
      <a class="eval-panel__link" href={devUrl} target="_blank" rel="noopener noreferrer">
        Abrir benchmarks no dev ↗
      </a>
    {/if}
  </div>
</details>

<style>
  .eval-panel summary {
    cursor: pointer;
    list-style: none;
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--foreground);
  }
  .eval-panel summary::-webkit-details-marker {
    display: none;
  }
  .eval-panel__body {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-top: var(--space-2);
    padding-top: var(--space-3);
    border-top: 1px dashed var(--border);
  }
  .eval-panel__explainer {
    margin: 0;
    font-size: 0.8rem;
    color: var(--muted-foreground);
  }
  .eval-panel__rate {
    margin: 0;
    font-size: 0.85rem;
    font-weight: 500;
  }
  .eval-panel__list {
    list-style: none;
    margin: var(--space-1) 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .eval-panel__item {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    font-size: 0.82rem;
  }
  .eval-panel__glyph {
    flex-shrink: 0;
    width: 1.1rem;
    font-weight: 700;
    text-align: center;
  }
  .eval-panel__item[data-status='incorrect'] .eval-panel__glyph {
    color: var(--destructive);
  }
  .eval-panel__item[data-status='needs_review'] .eval-panel__glyph {
    color: var(--warning);
  }
  .eval-panel__item[data-status='correct'] .eval-panel__glyph {
    color: var(--success);
  }
  .eval-panel__question {
    min-width: 0;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--foreground);
  }
  .eval-panel__link {
    align-self: flex-start;
    margin-top: var(--space-1);
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--accent-hover);
  }
</style>

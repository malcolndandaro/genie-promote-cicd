<script lang="ts">
  import type { TimelineStep, StepStatus } from '../types';
  import { STATUS_GLYPH, STATUS_LABEL } from '../pipeline';

  interface Props {
    steps: TimelineStep[];
    /** True while the review request is in flight — shows the animated "running" pipeline. */
    running?: boolean;
  }
  let { steps, running = false }: Props = $props();

  // While running, every node is shown as "awaiting" (the comet conveys activity); when the real
  // result lands, nodes reveal their true status in a staggered cascade. Honest: no fabricated
  // per-step verdicts before the server returns them.
  const shownStatus = (s: TimelineStep): StepStatus | 'await' => (running ? 'await' : s.status);

  // The colored rail fill covers the UNINTERRUPTED passing prefix (stops at the first non-pass),
  // so it reads as "succeeded this far" — never paints a failed/pending region as done.
  let firstNonPass = $derived(steps.findIndex((s) => s.status !== 'pass'));
  let leadingPass = $derived(firstNonPass === -1 ? steps.length : firstNonPass);
  let fillPct = $derived(running || steps.length === 0 ? 0 : Math.round((leadingPass / steps.length) * 100));
</script>

<div class="pipeline" aria-label="Pipeline de promoção" style:--fill="{fillPct}%">
  <span class="pipeline__rail" aria-hidden="true"></span>
  <span class="pipeline__fill" aria-hidden="true"></span>
  {#if running}<span class="pipeline__comet" aria-hidden="true"></span>{/if}

  <ul class="steps">
    {#each steps as s, i (s.key)}
      <li class="step" style:--i={i}>
        <span class="node" data-status={shownStatus(s)} aria-hidden="true">
          {#if !running && (s.status === 'pass' || s.status === 'fail' || s.status === 'running')}
            <span class="node__glyph">{STATUS_GLYPH[s.status]}</span>
          {/if}
        </span>
        <span class="step__label" data-status={shownStatus(s)}>
          {s.label}
          <!-- Status in words: color/glyph must not be the only signal (WCAG 1.4.1). -->
          <span class="visually-hidden">— {running ? 'em execução' : STATUS_LABEL[s.status]}</span>
        </span>
        <!-- G8: "por que falhou?" without leaving the app — the CI's own PT findings, per failing
             check run, plus a GitHub link as the fallback. Only ever set on a failed `checks` step. -->
        {#if !running && s.status === 'fail' && s.detail && s.detail.length > 0}
          <details class="check-detail">
            <summary>Ver detalhes das checagens ({s.detail.length})</summary>
            <ul class="check-detail__list">
              {#each s.detail as d, di (d.name + di)}
                <li class="check-detail__item">
                  <div class="check-detail__head">
                    <span class="check-detail__name">{d.name}</span>
                    <span class="check-detail__conclusion">{d.conclusion}</span>
                  </div>
                  {#if d.summary}<pre class="check-detail__summary">{d.summary}</pre>{/if}
                  {#if d.details_url}
                    <a class="check-detail__link" href={d.details_url} target="_blank" rel="noopener noreferrer">
                      Ver log no GitHub ↗
                    </a>
                  {/if}
                </li>
              {/each}
            </ul>
          </details>
        {/if}
      </li>
    {/each}
  </ul>
</div>

<style>
  .pipeline {
    position: relative;
    padding-left: 2.5rem;
  }
  .pipeline__rail,
  .pipeline__fill {
    position: absolute;
    left: 13px;
    width: 2px;
    border-radius: 2px;
  }
  .pipeline__rail {
    top: 13px;
    bottom: 13px;
    background: var(--border);
  }
  .pipeline__fill {
    top: 13px;
    height: var(--fill, 0%);
    background: var(--success);
    transition: height 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  }
  /* The signature "running" effect: a comet of light travelling down the rail. */
  .pipeline__comet {
    position: absolute;
    left: 10px;
    width: 8px;
    height: 26px;
    border-radius: 8px;
    background: linear-gradient(180deg, transparent, var(--accent));
    filter: blur(1px);
    animation: comet 1.5s ease-in-out infinite;
  }
  @keyframes comet {
    0% {
      top: 6px;
      opacity: 0;
    }
    15% {
      opacity: 1;
    }
    85% {
      opacity: 1;
    }
    100% {
      top: calc(100% - 30px);
      opacity: 0;
    }
  }

  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    position: relative;
  }
  .step {
    position: relative;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.85rem;
    min-height: 2.3rem;
  }
  .node {
    position: absolute;
    left: -2.5rem;
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: var(--surface);
    border: 2px solid var(--border-strong);
    color: var(--muted-foreground);
    font-size: 0.72rem;
    font-weight: 700;
    /* Staggered reveal: nodes colour in sequence when the result lands. */
    transition:
      background-color 0.35s ease,
      border-color 0.35s ease,
      color 0.35s ease,
      box-shadow 0.35s ease;
    transition-delay: calc(var(--i) * 0.12s);
  }
  .node[data-status='pass'] {
    background: var(--success);
    border-color: var(--success);
    color: var(--success-foreground);
    box-shadow: 0 0 0 4px var(--success-soft);
  }
  .node[data-status='fail'] {
    background: var(--destructive);
    border-color: var(--destructive);
    color: var(--destructive-foreground);
    box-shadow: 0 0 0 4px var(--destructive-soft);
  }
  .node[data-status='running'] {
    border-color: var(--warning);
    color: var(--warning);
    box-shadow: 0 0 0 4px var(--warning-soft);
  }
  .node[data-status='await'] {
    border-color: var(--border-strong);
    animation: node-pulse 1.5s ease-in-out infinite;
    animation-delay: calc(var(--i) * 0.18s);
  }
  @keyframes node-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 var(--accent-soft);
      border-color: var(--border-strong);
    }
    50% {
      box-shadow: 0 0 0 5px var(--accent-soft);
      border-color: var(--accent);
    }
  }
  .node[data-status='running'] .node__glyph {
    animation: node-spin 1.4s linear infinite;
  }

  .step__label {
    font-size: 0.875rem;
    color: var(--muted-foreground);
    transition: color 0.35s ease;
    transition-delay: calc(var(--i) * 0.12s);
  }
  .step__label[data-status='pass'],
  .step__label[data-status='fail'] {
    color: var(--foreground);
  }

  /* G8: forced onto its own line within the wrapping flex row (flex-basis: 100%) — the standard
     flexbox trick, no separate grid/column layout needed for one occasional full-width block. */
  .check-detail {
    flex: 1 0 100%;
    margin-top: 0.15rem;
  }
  .check-detail summary {
    cursor: pointer;
    list-style: none;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--destructive);
  }
  .check-detail summary::-webkit-details-marker {
    display: none;
  }
  .check-detail__list {
    list-style: none;
    margin: var(--space-2) 0 0;
    padding: var(--space-3) 0 0;
    border-top: 1px dashed var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .check-detail__item {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .check-detail__head {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
  }
  .check-detail__name {
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--foreground);
  }
  .check-detail__conclusion {
    font-size: 0.75rem;
    color: var(--destructive);
  }
  .check-detail__summary {
    margin: 0;
    padding: var(--space-2) var(--space-3);
    background: var(--destructive-soft);
    border: 1px solid color-mix(in srgb, var(--destructive) 25%, transparent);
    border-radius: var(--radius-sm);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.78rem;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--foreground);
  }
  .check-detail__link {
    align-self: flex-start;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--accent-hover);
  }

  @keyframes node-spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>

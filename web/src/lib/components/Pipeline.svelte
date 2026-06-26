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

  @keyframes node-spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>

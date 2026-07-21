<script lang="ts">
  // DriftPanel: renders a READ-ONLY GitHub-gate drift report. No longer actively used in R1 —
  // the drift feature was retired in favour of the Settings GitHub mirror (GET /admin/github-roles).
  // Kept as a tombstone in case it's needed for future debug/comparison surfaces.
  import Badge from './Badge.svelte';

  // Inline type since DriftReport was removed from types.ts in R1.
  interface DriftFinding {
    kind: string;
    severity: 'warning' | 'unknown';
    message: string;
    detail: Record<string, unknown>;
  }
  interface DriftReport {
    has_drift: boolean;
    has_unknown: boolean;
    findings: DriftFinding[];
  }

  interface Props {
    report: DriftReport;
    /** Compact renders a single summary line + a details disclosure (for the review screen); the
     * full form always lists every finding (for Settings). */
    compact?: boolean;
  }
  let { report, compact = false }: Props = $props();
</script>

{#if report.findings.length === 0}
  {#if !compact}
    <p class="clean" role="status">
      <span class="clean__dot" aria-hidden="true"></span>
      Nenhuma divergência encontrada entre os papéis do app e os gates do GitHub.
    </p>
  {/if}
{:else if compact}
  <details class="drift-compact">
    <summary>
      <Badge tone={report.has_unknown ? 'neutral' : 'warning'}>
        {report.has_unknown && !report.has_drift
          ? 'Verificação de gate indisponível'
          : `${report.findings.length} divergência(s) de governança`}
      </Badge>
    </summary>
    <ul class="findings">
      {#each report.findings as f (f.kind + JSON.stringify(f.detail))}
        <li class="finding finding--{f.severity}">{f.message}</li>
      {/each}
    </ul>
  </details>
{:else}
  <ul class="findings">
    {#each report.findings as f (f.kind + JSON.stringify(f.detail))}
      <li class="finding finding--{f.severity}">
        <Badge tone={f.severity === 'unknown' ? 'neutral' : 'warning'}>
          {f.severity === 'unknown' ? 'Desconhecido' : 'Divergência'}
        </Badge>
        <span>{f.message}</span>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .clean {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.85rem;
    color: var(--muted-foreground);
    margin: 0;
  }
  .clean__dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--success);
    flex-shrink: 0;
  }
  .findings {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .finding {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    font-size: 0.85rem;
    line-height: 1.4;
  }
  .drift-compact summary {
    cursor: pointer;
    list-style: none;
  }
  .drift-compact summary::-webkit-details-marker {
    display: none;
  }
  .drift-compact .findings {
    margin-top: var(--space-3);
    padding-top: var(--space-3);
    border-top: 1px dashed var(--border);
  }
  .drift-compact .finding {
    flex-direction: column;
    gap: 0.35rem;
  }
</style>

/**
 * Promotion pipeline presentation metadata.
 *
 * The real per-step verdicts come from the server (`review.timeline`, built by
 * `app_logic.build_timeline`). These static steps mirror that order and are used ONLY to render
 * the pipeline while the review request is in flight (before the result exists) — an HONEST
 * progress animation, not a fabricated verdict. When the result arrives, `review.timeline`
 * overrides labels + statuses.
 */
import type { StepStatus, TimelineStep } from './types';

export const PIPELINE_STEPS: { key: string; label: string }[] = [
  { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)' },
  { key: 'review', label: 'Revisão do agente (Genie Reviewer)' },
  { key: 'eval', label: 'Eval-run' },
  { key: 'approval', label: 'Aprovação do Steward' },
  { key: 'deploy', label: 'Deploy em produção (service principal)' },
];

/** The steps to show while the review is running (all pending; the animation conveys activity). */
export function pendingTimeline(): TimelineStep[] {
  return PIPELINE_STEPS.map((s) => ({ ...s, status: 'pending' as StepStatus }));
}

/** Glyph per status (a hollow ring for pending is drawn in CSS). */
export const STATUS_GLYPH: Record<StepStatus, string> = {
  pass: '✓',
  fail: '✕',
  running: '◴',
  pending: '',
};

/** Status conveyed in words (for screen readers — color/glyph must not be the only signal). */
export const STATUS_LABEL: Record<StepStatus, string> = {
  pass: 'concluído',
  fail: 'reprovado',
  running: 'em execução',
  pending: 'pendente',
};

/** Severity → badge tone (matches the retired AppKit SEVERITY_VARIANT mapping). */
export function severityTone(severity: string): 'destructive' | 'warning' | 'neutral' {
  if (severity === 'BLOCKER') return 'destructive';
  if (severity === 'SUGGESTION') return 'warning';
  return 'neutral';
}

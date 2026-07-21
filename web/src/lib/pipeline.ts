/**
 * Promotion pipeline presentation metadata.
 *
 * The real per-step verdicts come from the server (`review.timeline`, built by
 * `app_logic.build_timeline`). These static steps mirror that order and are used ONLY to render
 * the pipeline while the review request is in flight (before the result exists) — an HONEST
 * progress animation, not a fabricated verdict. When the result arrives, `review.timeline`
 * overrides labels + statuses.
 */
import type { Review } from './types';
import type { StepStatus, TimelineStep, EvalQuestion } from './types';
import type { PromoteStatus } from './api';

export const PIPELINE_STEPS: { key: string; label: string }[] = [
  { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)' },
  { key: 'review', label: 'Revisão do agente (Genie Reviewer)' },
  { key: 'eval', label: 'Eval-run' },
  { key: 'pr_review', label: 'Revisão do PR (aprovação de merge)' },
  { key: 'merge', label: 'Merge para main' },
  { key: 'approval', label: 'Aprovação da Plataforma (deploy)' },
  { key: 'deploy', label: 'Deploy em produção (service principal)' },
];

/** The steps to show while the review is running (all pending; the animation conveys activity). */
export function pendingTimeline(): TimelineStep[] {
  return PIPELINE_STEPS.map((s) => ({ ...s, status: 'pending' as StepStatus }));
}

/** Label fallbacks for the verdict steps if the server omits one (it always emits all three). */
const VERDICT_LABELS: Record<string, string> = {
  checks: 'Checagens determinísticas (pré-render + allowlist)',
  review: 'Revisão do agente (Genie Reviewer)',
  eval: 'Eval-run',
};

/**
 * Build the 7-step promotion pipeline (GH5): the first 3 come from the review VERDICT
 * (`review.timeline` — checks/review/eval, preserving the server's labels + statuses), the last 4
 * are driven LIVE by the polled `get_status` (pr_review/merge/approval/deploy). Reflect, never
 * assert — a step is `pass` only when GitHub reports it, otherwise pending/running.
 */
export function buildPromotionSteps(review: Review, live: PromoteStatus | null): TimelineStep[] {
  const byKey = new Map(review.timeline.map((s) => [s.key, s]));
  const verdict: TimelineStep[] = ['checks', 'review', 'eval'].map((key) => {
    const s = byKey.get(key) ?? { key, label: VERDICT_LABELS[key], status: 'pending' as StepStatus };
    if (key !== 'checks') return s;
    // G8: the app's own "checks" verdict is a PRE-PR preview (pre-render + allowlist + a grant
    // preview); the live GitHub PR check-run (bundle validate + Audience preflight, as the prod SP, with
    // real prod visibility) is the AUTHORITATIVE run of the same gate — escalate to `fail` +
    // attach its own PT-friendly detail even when the preview said `pass`.
    if (live?.checks === 'failure') {
      return { ...s, status: 'fail' as StepStatus, detail: live.checks_detail ?? null };
    }
    return s;
  });

  const prReview: StepStatus = live?.merged
    ? 'pass'
    : live?.review_decision === 'approved'
      ? 'pass'
      : live?.review_decision === 'changes_requested'
        ? 'fail'
        : live
          ? 'running'
          : 'pending';

  const merge: StepStatus = live?.merged
    ? 'pass'
    : // 'running' = all merge preconditions met (can't merge until checks pass + the PR is approved).
      live && live.checks === 'success' && live.review_decision === 'approved'
      ? 'running'
      : 'pending';

  const approval: StepStatus =
    live?.deploy.status === 'waiting'
      ? 'running'
      : // 'completed' here means the Environment gate was RELEASED, not that the deploy succeeded —
        // the deploy step below carries the run's success/failure.
        live && ['queued', 'in_progress', 'completed'].includes(live.deploy.status)
        ? 'pass'
        : 'pending';

  const deployFailed = live?.phase === 'deploy_failed';
  const deploy: StepStatus = live?.phase === 'deployed' ? 'pass' : deployFailed ? 'fail'
    : live?.phase === 'deploying' ? 'running' : 'pending';
  // Fix C: WHY the deploy failed, rendered by the SAME <details> panel `checks` already uses —
  // adapt the single `deploy_detail` into a one-item `CheckDetail[]`-shaped list, no new UI code.
  const deployDetail = deployFailed && live?.deploy_detail
    ? [{
        name: live.deploy_detail.failed_step,
        conclusion: 'failure',
        summary: live.deploy_detail.summary,
        details_url: live.deploy_detail.details_url,
      }]
    : null;

  return [
    ...verdict,
    { key: 'pr_review', label: 'Revisão do PR (aprovação de merge)', status: prReview },
    { key: 'merge', label: 'Merge para main', status: merge },
    { key: 'approval', label: 'Aprovação da Plataforma (deploy)', status: approval },
    { key: 'deploy', label: 'Deploy em produção (service principal)', status: deploy, detail: deployDetail },
  ];
}

/** PT label for the failing-step `<details>` summary (G8 checks / Fix C deploy), keyed by step —
 * "checagens" is wrong wording for a deploy failure, so this isn't one generic string. */
export const DETAIL_SUMMARY_LABEL: Record<string, string> = {
  checks: 'Ver detalhes das checagens',
  deploy: 'Ver detalhes do deploy',
};

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
  if (severity === 'OPERATIONAL') return 'destructive';
  if (severity === 'SUGGESTION') return 'warning';
  return 'neutral';
}

/** W3: glyph per eval per-question outcome — mirrors STATUS_GLYPH's role for the pipeline nodes,
 * used by EvalResultsPanel's failures-first list (✗/⚠/✓, never color alone). */
export const EVAL_QUESTION_GLYPH: Record<EvalQuestion['status'], string> = {
  incorrect: '✗',
  needs_review: '⚠',
  correct: '✓',
};

/** PT label in words for the same outcome (WCAG 1.4.1 — color/glyph must not be the only signal). */
export const EVAL_QUESTION_LABEL: Record<EvalQuestion['status'], string> = {
  incorrect: 'incorreta',
  needs_review: 'requer revisão',
  correct: 'correta',
};

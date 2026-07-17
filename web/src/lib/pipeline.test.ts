import { describe, it, expect } from 'vitest';
import { buildPromotionSteps } from './pipeline';
import type { Review } from './types';
import type { PromoteStatus } from './api';

const review: Review = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' },
    { key: 'eval', label: 'Eval-run (advisory)', status: 'pass' },
  ],
};

const liveStatus = (over: Partial<PromoteStatus> = {}): PromoteStatus => ({
  pr_state: 'open',
  merged: false,
  checks: 'success',
  review_decision: 'approved',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null, run_id: null, approver: null },
  pr_url: 'https://github.com/o/r/pull/1',
  phase: 'open',
  ...over,
});

// The live GitHub PR check-run is
// the AUTHORITATIVE run of the same gate the app previewed at review time — it must win even when
// the app's own pre-PR preview (`review.timeline`) said `pass`.
describe('buildPromotionSteps — G8 checks_detail', () => {
  it('leaves the checks step alone when live checks have not failed', () => {
    const steps = buildPromotionSteps(review, liveStatus());
    const checks = steps.find((s) => s.key === 'checks');
    expect(checks?.status).toBe('pass');
    expect(checks?.detail).toBeUndefined();
  });

  it('leaves the checks step alone before any live status has landed (null)', () => {
    const steps = buildPromotionSteps(review, null);
    expect(steps.find((s) => s.key === 'checks')?.status).toBe('pass');
  });

  it('escalates the checks step to fail and attaches checks_detail on a live GitHub failure', () => {
    const detail = [
      { name: 'AUDIENCE-01', conclusion: 'failure',
        summary: "'users' não tem SELECT em prod_recebiveis.diamond.dim_arranjo",
        details_url: 'https://github.com/o/r/pull/1/checks' },
    ];
    const steps = buildPromotionSteps(
      review,
      liveStatus({ checks: 'failure', phase: 'checks_failed', checks_detail: detail }),
    );
    const checks = steps.find((s) => s.key === 'checks');
    expect(checks?.status).toBe('fail');
    expect(checks?.detail).toEqual(detail);
    // The other verdict steps (review/eval) are untouched by the live-checks escalation.
    expect(steps.find((s) => s.key === 'review')?.status).toBe('pass');
  });

  it('escalates to fail with detail=null when the bot could not fetch check-run detail', () => {
    const steps = buildPromotionSteps(
      review,
      liveStatus({ checks: 'failure', phase: 'checks_failed', checks_detail: null }),
    );
    const checks = steps.find((s) => s.key === 'checks');
    expect(checks?.status).toBe('fail');
    expect(checks?.detail).toBeNull();
  });
});

// Fix C: mirrors G8 one level down — a DEPLOY failure (e.g. audience reconciliation failing
// mid-attempt) must surface WHY, not a bare "Falha", via the same <details> mechanism.
describe('buildPromotionSteps — Fix C deploy_detail', () => {
  it('leaves the deploy step pending with no detail before any live status has landed', () => {
    const steps = buildPromotionSteps(review, null);
    const deploy = steps.find((s) => s.key === 'deploy');
    expect(deploy?.status).toBe('pending');
    expect(deploy?.detail).toBeNull();
  });

  it('does not attach detail when the deploy succeeded', () => {
    const steps = buildPromotionSteps(review, liveStatus({ phase: 'deployed' }));
    const deploy = steps.find((s) => s.key === 'deploy');
    expect(deploy?.status).toBe('pass');
    expect(deploy?.detail).toBeNull();
  });

  it('marks the deploy step fail and attaches the adapted deploy_detail on a live failure', () => {
    const steps = buildPromotionSteps(
      review,
      liveStatus({
        phase: 'deploy_failed',
        deploy_detail: {
          failed_step: 'Apply declared access',
          summary: "AttributeError: 'dict' object has no attribute 'as_dict'",
          details_url: 'https://github.com/o/r/actions/runs/9/jobs/101',
        },
      }),
    );
    const deploy = steps.find((s) => s.key === 'deploy');
    expect(deploy?.status).toBe('fail');
    expect(deploy?.detail).toEqual([
      {
        name: 'Apply declared access',
        conclusion: 'failure',
        summary: "AttributeError: 'dict' object has no attribute 'as_dict'",
        details_url: 'https://github.com/o/r/actions/runs/9/jobs/101',
      },
    ]);
  });

  it('sets detail=null when the deploy failed but the bot could not fetch job detail', () => {
    const steps = buildPromotionSteps(
      review,
      liveStatus({ phase: 'deploy_failed', deploy_detail: null }),
    );
    const deploy = steps.find((s) => s.key === 'deploy');
    expect(deploy?.status).toBe('fail');
    expect(deploy?.detail).toBeNull();
  });
});

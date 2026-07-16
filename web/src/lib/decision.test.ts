import { describe, expect, it } from 'vitest';
import { decisionPresentation } from './decision';
import type { PromoteStatus } from './api';
import type { Review } from './types';

const review = (blocked = false): Review => ({
  findings: blocked ? [{ rule_id: 'ENV-01', severity: 'BLOCKER', message: 'fora do ambiente' }] : [],
  gate: { conclusion: blocked ? 'failure' : 'success', blocker_count: blocked ? 1 : 0, summary: 'x' },
  eval: { status: 'advisory', summary: 'x' }, allowlist_violations: [], consumer_group: '', timeline: [],
});

const status = (over: Partial<PromoteStatus> = {}): PromoteStatus => ({
  pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
  pr_url: 'https://gh/pr/1', phase: 'open', ...over,
});

const attempt = (terminal_state: 'running' | 'operational_failed' | 'partial_failed' | 'succeeded',
  mutation_started: boolean, run_attempt = 1) => ({
  attempt_id: `github:9:${run_attempt}`, run_attempt,
  revisions: { content_revision: 'b'.repeat(64), engine_revision: 'a'.repeat(40) },
  mutation_started, completed_stages: ['preflight'], current_stage: 'bundle_deploy',
  failed_stage: null, target_ids: {}, reason: null, run_url: 'https://gh/run/9', terminal_state,
});

describe('decisionPresentation', () => {
  it('renders the approved ready, blocked and checks-running language', () => {
    expect(decisionPresentation(review(), null).headline).toBe('Pronto para solicitar a promoção');
    expect(decisionPresentation(review(true), null).headline).toBe('Ajustes necessários antes de promover');
    expect(decisionPresentation(review(), status({ phase: 'checks_running' })).headline)
      .toBe('As checagens estão em andamento');
  });

  it('distinguishes partial, pre-mutation operational failure and recovery', () => {
    const partial = decisionPresentation(review(), status({ phase: 'deploy_failed', deploy: {
      status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: 'x',
      attempt: attempt('partial_failed', true),
    } }));
    expect(partial.state).toBe('partial');
    expect(partial.productionTruth).toContain('mudou parcialmente');

    const operational = decisionPresentation(review(), status({ phase: 'deploy_failed', deploy: {
      status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: 'x',
      attempt: attempt('operational_failed', false),
    } }));
    expect(operational.headline).toBe('Não conseguimos iniciar a publicação');
    expect(operational.productionTruth).toContain('Produção não mudou');

    const resuming = decisionPresentation(review(), status({ phase: 'deploying', deploy: {
      status: 'in_progress', conclusion: null, waiting_approval: false, run_url: 'x',
      attempt: attempt('running', false, 2),
    } }));
    expect(resuming.headline).toBe('A KIP está retomando a publicação');
  });

  it('never claims zero or partial mutation when Attempt evidence is missing', () => {
    const unknown = decisionPresentation(review(), status({ phase: 'deploy_failed' }));
    expect(unknown.productionTruth).toContain('Não foi possível confirmar');
    expect(unknown.productionTruth).not.toContain('não mudou');
    expect(unknown.productionTruth).not.toContain('mudou parcialmente');
  });
});

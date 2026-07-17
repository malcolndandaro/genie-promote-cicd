import { expect, test, type Page } from '@playwright/test';
import { confirmPilotPromotion } from './promotion-helpers';

const PR = { number: 42, url: 'https://github.com/o/r/pull/42' };
const cleanReview = {
  findings: [], gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto.' },
  eval: { status: 'advisory', summary: 'Sem eval.' }, allowlist_violations: [],
  timeline: [
    { key: 'checks', label: 'Checagens', status: 'pass' },
    { key: 'review', label: 'Revisão', status: 'pass' },
    { key: 'eval', label: 'Eval-run', status: 'pass' },
  ],
};
const baseStatus = {
  provider: 'github', external_id: '42', external_url: PR.url,
  pr_state: 'open', merged: false, checks: 'success', review_decision: 'approved',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
  pr_url: PR.url, phase: 'open',
};
const attempt = (state: string, mutationStarted: boolean, runAttempt = 1) => ({
  version: 1, attempt_id: `github:900:${runAttempt}`, run_attempt: runAttempt,
  revisions: { content_revision: 'b'.repeat(64), engine_revision: 'a'.repeat(40) },
  mutation_started: mutationStarted,
  completed_stages: mutationStarted ? ['preflight', 'bundle_deploy', 'resolve_space', 'assert_app_manage'] : [],
  current_stage: mutationStarted ? 'reconcile_audience' : 'preflight',
  failed_stage: state === 'partial_failed' ? 'reconcile_audience' : state === 'operational_failed' ? 'preflight' : null,
  target_ids: mutationStarted ? { receivables: 'space-prod-1' } : {},
  reason: state.includes('failed') ? 'InvalidParameterValue: principal unavailable' : null,
  run_url: 'https://github.com/o/r/actions/runs/900', terminal_state: state, sequence: 8,
});
const githubSteps = (stagedConclusion: string = 'failure') => [
  { name: 'Set up job', status: 'completed', conclusion: 'success', number: 1,
    job_name: 'Promote to prod (Steward approval)', details_url: 'https://github.com/o/r/actions/runs/900/jobs/1' },
  { name: 'Checkout content (merged main)', status: 'completed', conclusion: 'success', number: 2,
    job_name: 'Promote to prod (Steward approval)', details_url: 'https://github.com/o/r/actions/runs/900/jobs/1' },
  { name: 'Safe staged deployment (preflight + forward-only reconciliation)', status: 'completed',
    conclusion: stagedConclusion, number: 9, job_name: 'Promote to prod (Steward approval)',
    details_url: 'https://github.com/o/r/actions/runs/900/jobs/1' },
  { name: 'Post Checkout content (merged main)', status: 'completed', conclusion: 'skipped', number: 10,
    job_name: 'Promote to prod (Steward approval)', details_url: 'https://github.com/o/r/actions/runs/900/jobs/1' },
];

async function start(page: Page, review: any, statuses: any[]): Promise<void> {
  let poll = 0;
  await page.route('**/api/whoami', (route) => route.fulfill({ json: {
    email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false,
  } }));
  await page.route('**/api/spaces', (route) => route.fulfill({ json: {
    spaces: [{ space_id: 'sp1', title: 'Recebíveis' }],
  } }));
  await page.route('**/api/promotions?scope=mine', (route) => route.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/promotions/p1/audit', (route) => route.fulfill({ json: { audit: [] } }));
  await page.route('**/api/promote/42/status', (route) => {
    const status = statuses[Math.min(poll++, statuses.length - 1)];
    return route.fulfill({ json: status });
  });
  const blocked = review.gate.conclusion === 'failure';
  await page.route('**/api/promote', (route) => route.fulfill({ json: {
    review, pr: blocked ? null : PR, promotion_id: blocked ? undefined : 'p1', blocked,
  } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);
}

test('ready, content-blocked and checks-running put the decision first', async ({ page }) => {
  await start(page, cleanReview, [{ ...baseStatus, phase: 'checks_running', checks: 'pending' }]);
  await expect(page.getByRole('heading', { name: 'As checagens estão em andamento' })).toBeVisible();
  await expect(page.getByText('Produção ainda não mudou', { exact: false })).toBeVisible();
  await expect(page.getByText('não envie outra solicitação', { exact: false })).toBeVisible();
});

test('ready review says no production mutation and points to the existing Change Request', async ({ page }) => {
  await start(page, cleanReview, [baseStatus]);
  await expect(page.getByRole('heading', { name: 'Pronto para solicitar a promoção' })).toBeVisible();
  await expect(page.getByText('Nenhuma mutação em produção', { exact: false })).toBeVisible();
  await expect(page.getByText('Acompanhe o Change Request aberto', { exact: false })).toBeVisible();
});

test('blockers stay visible while advisory guidance is collapsed', async ({ page }) => {
  const blocked = {
    ...cleanReview,
    findings: [
      { rule_id: 'ENV-01', severity: 'BLOCKER', message: 'Tabela aponta para outro ambiente.' },
      { rule_id: 'KA:guide', severity: 'SUGGESTION', message: 'Considere melhorar a descrição.' },
    ],
    gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Bloqueado.' },
  };
  await start(page, blocked, [baseStatus]);
  await expect(page.getByRole('heading', { name: 'Ajustes necessários antes de promover' })).toBeVisible();
  await expect(page.getByText('PR de promoção aberto:')).toHaveCount(0);
  await expect(page.getByText('Tabela aponta para outro ambiente.')).toBeVisible();
  await expect(page.getByText('Considere melhorar a descrição.')).not.toBeVisible();
  await page.getByText('Orientações não bloqueantes (1)', { exact: true }).click();
  await expect(page.getByText('Considere melhorar a descrição.')).toBeVisible();
});

test('partial failure shows changed production and exact support evidence without retry', async ({ page }) => {
  const partial = attempt('partial_failed', true);
  await start(page, cleanReview, [{ ...baseStatus, merged: true, phase: 'deploy_failed', deploy: {
    status: 'completed', conclusion: 'failure', waiting_approval: false,
    run_url: partial.run_url, attempt: partial, steps: githubSteps(),
  } }]);
  await expect(page.getByRole('heading', { name: 'A publicação parou depois de alterar produção' })).toBeVisible();
  await expect(page.getByText('Produção mudou parcialmente', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toHaveCount(0);

  await page.getByText('Detalhes para suporte e auditoria', { exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Steps reais do job de deploy' })).toBeVisible();
  await expect(page.getByText('GitHub Actions · fonte oficial', { exact: true })).toBeVisible();
  await expect(page.locator('.deploy-steps li')).toHaveCount(4);
  await expect(page.locator('.deploy-steps li[data-state="failed"]')).toContainText(
    'Safe staged deployment (preflight + forward-only reconciliation)',
  );
  await expect(page.locator('.deploy-steps li[data-state="failed"]')).toContainText('Falhou');
  await expect(page.getByText('Resolver Space', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Deployment Attempt' })).toBeVisible();
  await expect(page.getByText('github:900:1', { exact: true })).toBeVisible();
  await expect(page.getByText('receivables=space-prod-1', { exact: true })).toBeVisible();
  await expect(page.getByText(/InvalidParameterValue/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Abrir run exato/ })).toHaveAttribute('href', partial.run_url);
  await expect(page.getByText('b'.repeat(64), { exact: true })).toBeVisible();
});

test('preflight failure explicitly proves no production mutation', async ({ page }) => {
  const failed = attempt('operational_failed', false);
  await start(page, cleanReview, [{ ...baseStatus, merged: true, phase: 'deploy_failed', deploy: {
    status: 'completed', conclusion: 'failure', waiting_approval: false,
    run_url: failed.run_url, attempt: failed,
  } }]);
  await expect(page.getByRole('heading', { name: 'Não conseguimos iniciar a publicação' })).toBeVisible();
  await expect(page.getByText('Produção não mudou', { exact: false })).toBeVisible();
  await expect(page.getByText('Nenhuma ação sua', { exact: false })).toBeVisible();
});

test('missing Attempt evidence never guesses mutation status', async ({ page }) => {
  await start(page, cleanReview, [{ ...baseStatus, merged: true, phase: 'deploy_failed', deploy: {
    status: 'completed', conclusion: 'failure', waiting_approval: false,
    run_url: 'https://github.com/o/r/actions/runs/unknown',
  } }]);
  await expect(page.getByText('Não foi possível confirmar se produção mudou', { exact: false })).toBeVisible();
  await page.getByText('Detalhes para suporte e auditoria', { exact: true }).click();
  await expect(page.getByText(/estado de mutação não será inferido/)).toBeVisible();
});

test('a partial failure keeps polling and becomes KIP resuming with the same revisions', async ({ page }) => {
  const partial = attempt('partial_failed', true);
  const resuming = attempt('running', false, 2);
  await start(page, cleanReview, [
    { ...baseStatus, merged: true, phase: 'deploy_failed', deploy: {
      status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: partial.run_url, attempt: partial,
    } },
    { ...baseStatus, merged: true, phase: 'deploying', deploy: {
      status: 'in_progress', conclusion: null, waiting_approval: false, run_url: resuming.run_url, attempt: resuming,
    } },
  ]);
  await expect(page.getByRole('heading', { name: 'A publicação parou depois de alterar produção' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'A KIP está retomando a publicação' })).toBeVisible({ timeout: 7000 });
  await page.getByText('Detalhes para suporte e auditoria', { exact: true }).click();
  await expect(page.getByText('github:900:2', { exact: true })).toBeVisible();
  await expect(page.getByText('a'.repeat(40), { exact: true })).toBeVisible();
});

test('GitHub job steps stay readable and inside a phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const partial = attempt('partial_failed', true);
  await start(page, cleanReview, [{ ...baseStatus, merged: true, phase: 'deploy_failed', deploy: {
    status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: partial.run_url,
    attempt: partial, steps: githubSteps(),
  } }]);
  await page.getByText('Detalhes para suporte e auditoria', { exact: true }).click();
  await expect(page.locator('.deploy-steps li')).toHaveCount(4);
  const widths = await page.evaluate(() => ({ client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
});

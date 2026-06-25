import { test, expect } from '@playwright/test';

const PR = { number: 42, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42' };
const RUN_URL = 'https://github.com/malcolndandaro/genie-promote-cicd/actions/runs/9';

const cleanReview = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' },
    { key: 'eval', label: 'Eval-run (advisory)', status: 'pass' },
    { key: 'approval', label: 'Aprovação do Steward', status: 'running' },
    { key: 'deploy', label: 'Deploy em produção (service principal)', status: 'pending' },
  ],
};

const blockerReview = {
  ...cleanReview,
  findings: [{ rule_id: 'EVAL-01', severity: 'BLOCKER', message: 'Poucas perguntas de benchmark.' }],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada.' },
  timeline: cleanReview.timeline.map((t) => (t.key === 'review' ? { ...t, status: 'fail' } : t)),
};

const deployStatus = (phase: string, extra: Record<string, unknown> = {}) => ({
  pr_state: 'closed',
  merged: true,
  checks: 'success',
  deploy: {
    status: phase === 'deployed' ? 'completed' : 'waiting',
    conclusion: phase === 'deployed' ? 'success' : null,
    waiting_approval: phase === 'awaiting_approval',
    run_url: RUN_URL,
    run_id: 9,
    approver: null,
    ...extra,
  },
  pr_url: PR.url,
  phase,
});

// Distinct requester (malcoln) and steward (pedro) — the SoD precondition.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
});

async function promote(
  page: import('@playwright/test').Page,
  review: unknown,
  status?: unknown,
) {
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } }));
  if (status) {
    await page.route(`**/api/promote/${PR.number}/status`, (route) => route.fulfill({ json: status }));
  }
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();
  await expect(page.getByRole('heading', { name: 'Aprovação do Steward' })).toBeVisible();
}

test('author cannot approve their own promotion (SoD)', async ({ page }) => {
  await promote(page, cleanReview);
  await expect(page.getByText(/não pode aprovar a própria promoção/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('steward is blocked from approving while a BLOCKER finding stands', async ({ page }) => {
  await promote(page, blockerReview);
  await page.getByRole('button', { name: 'Steward' }).click();
  await expect(page.getByText(/bloqueada por achados BLOCKER/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('a distinct steward gets the "Aprovar no GitHub" deep-link when the gate is waiting', async ({
  page,
}) => {
  await promote(page, cleanReview, deployStatus('awaiting_approval'));
  await page.getByRole('button', { name: 'Steward' }).click();
  const link = page.getByRole('link', { name: /Aprovar no GitHub/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', RUN_URL); // the bot never approves — the human does, on GitHub
});

test('reflects a completed deploy with the approver login', async ({ page }) => {
  await promote(page, cleanReview, deployStatus('deployed', { approver: 'pedro-gh' }));
  await page.getByRole('button', { name: 'Steward' }).click();
  await expect(page.getByText(/Implantado em produção — aprovado por pedro-gh/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('reflects a failed deploy (no approve link)', async ({ page }) => {
  await promote(
    page,
    cleanReview,
    deployStatus('deploy_failed', { status: 'completed', conclusion: 'failure' }),
  );
  await page.getByRole('button', { name: 'Steward' }).click();
  await expect(page.getByText(/Falha no deploy de produção/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('pre-merge, a distinct steward sees the "after merge" state (no premature deep-link)', async ({
  page,
}) => {
  await promote(page, cleanReview, {
    pr_state: 'open',
    merged: false,
    checks: 'pending',
    deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
    pr_url: PR.url,
    phase: 'checks_running',
  });
  await page.getByRole('button', { name: 'Steward' }).click();
  await expect(page.getByText(/Após o merge do PR/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('a steward configured as the requester (misconfig) still cannot self-approve (SoD)', async ({
  page,
}) => {
  await page.unroute('**/api/whoami');
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'malcoln@databricks.com' } }),
  );
  await promote(page, cleanReview, deployStatus('awaiting_approval'));
  await page.getByRole('button', { name: 'Steward' }).click();
  await expect(page.getByText(/o solicitante não pode aprovar a própria promoção/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

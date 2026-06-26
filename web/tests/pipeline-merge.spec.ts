import { test, expect } from '@playwright/test';

// GH5: the merge + PR-review (merge-approval) gate are first-class, LIVE-monitored pipeline steps.
// The first 3 steps come from the review verdict; pr_review/merge/approval/deploy are driven by the
// polled live status — reflect, never assert.

const PR = { number: 42, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42' };

const review = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' },
    { key: 'eval', label: 'Eval-run (advisory)', status: 'pass' },
  ],
};

const liveStatus = (over: Record<string, unknown> = {}) => ({
  pr_state: 'open',
  merged: false,
  checks: 'success',
  review_decision: 'approved',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null, run_id: null, approver: null },
  pr_url: PR.url,
  phase: 'open',
  ...over,
});

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } }));
});

async function promote(page: import('@playwright/test').Page, status: unknown) {
  await page.route(`**/api/promote/${PR.number}/status`, (route) => route.fulfill({ json: status }));
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();
}

// The Pipeline renders a visually-hidden status word per step inside its listitem
// ("— concluído"=pass, "— reprovado"=fail, "— em execução"=running, "— pendente"=pending),
// so we assert STATUS, not just visibility.
const step = (page: import('@playwright/test').Page, label: string) =>
  page.getByRole('listitem').filter({ hasText: label });

test('PR-review + Merge are distinct steps; approved/open → PR-review concluído, Merge em execução', async ({
  page,
}) => {
  await promote(page, liveStatus());
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  // The verdict steps are still present (unchanged from the review).
  await expect(page.getByText('Revisão do agente (Genie Reviewer)')).toBeVisible();
  // The PR is approved but not yet merged → PR-review passed, Merge is running (preconditions met).
  await expect(step(page, 'Revisão do PR (aprovação de merge)')).toContainText('concluído');
  await expect(step(page, 'Merge para main')).toContainText('em execução');
});

test('changes_requested → PR-review step shows reprovado', async ({ page }) => {
  await promote(page, liveStatus({ review_decision: 'changes_requested' }));
  await expect(step(page, 'Revisão do PR (aprovação de merge)')).toContainText('reprovado');
});

test('a merged PR shows the "Merge para main" step as concluído', async ({ page }) => {
  await promote(page, liveStatus({ merged: true, pr_state: 'closed', phase: 'merged' }));
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(step(page, 'Merge para main')).toContainText('concluído');
});

test('a failed deploy → Deploy step shows reprovado', async ({ page }) => {
  await promote(
    page,
    liveStatus({
      merged: true,
      pr_state: 'closed',
      deploy: { status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: 'r', run_id: 9, approver: null },
      phase: 'deploy_failed',
    }),
  );
  await expect(step(page, 'Deploy em produção (service principal)')).toContainText('reprovado');
});

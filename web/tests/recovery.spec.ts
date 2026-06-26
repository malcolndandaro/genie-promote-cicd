import { test, expect } from '@playwright/test';

// LB3: on load the SPA recovers the most-recent promotion from its STORED snapshot — it must NOT
// re-run the LLM reviewer (no POST /api/promote). The live status still comes from the GitHub poll.

const review = {
  findings: [
    {
      rule_id: 'GRANT-01',
      severity: 'BLOCKER',
      message: 'Grupo consumidor sem SELECT em prod_recebiveis.diamond.fato_recebiveis.',
      citation: 'Genie Promotion Handbook › Grants › GRANT-01',
      suggestion: 'Conceder SELECT ao grupo consumidor.',
    },
  ],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada — 1 achado BLOCKER.' },
  eval: { status: 'pass', summary: '🟢 eval-run ok.' },
  allowlist_violations: [],
  consumer_group: '',
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'fail' },
    { key: 'eval', label: 'Eval-run', status: 'pass' },
  ],
};

const PR = { number: 42, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42' };
const summary = {
  id: 'promo-1',
  resource_id: 'sp1',
  resource_kind: 'genie_space',
  resource_title: 'Recebíveis',
  pr_number: PR.number,
  pr_url: PR.url,
  current_phase: 'open',
  terminal: false,
  created_at: '2026-06-26T10:00:00Z',
  updated_at: '2026-06-26T10:05:00Z',
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'ana@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  await page.route(`**/api/promote/${PR.number}/status`, (route) =>
    route.fulfill({
      json: {
        pr_state: 'open',
        merged: false,
        checks: 'pending',
        review_decision: 'review_required',
        deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
        pr_url: PR.url,
        phase: 'checks_running',
      },
    }),
  );
});

test('recovers the stored promotion on load WITHOUT re-running the reviewer', async ({ page }) => {
  // Hard assertion: POST /api/promote (the reviewer re-run) must never fire during recovery.
  let promoteCalled = false;
  await page.route('**/api/promote', (route) => {
    promoteCalled = true;
    route.fulfill({ status: 500, json: { detail: 'promote must not be called on recovery' } });
  });

  await page.route('**/api/promotions?scope=mine', (route) =>
    route.fulfill({ json: { promotions: [summary] } }),
  );
  await page.route(`**/api/promotions/${summary.id}`, (route) =>
    route.fulfill({ json: { promotion: summary, review, pr: PR } }),
  );

  await page.goto('/');

  // The recovered promotion renders: PR banner + the stored finding + the live phase badge (polled).
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByText('GRANT-01', { exact: true })).toBeVisible();
  await expect(page.getByText(/Promoção bloqueada/)).toBeVisible();
  await expect(page.getByText('Checagens em execução')).toBeVisible(); // live status resumed
  await expect(page.getByRole('link', { name: /Ver no GitHub/ })).toHaveAttribute('href', PR.url);

  // The clincher: the reviewer was never re-run.
  expect(promoteCalled).toBe(false);
});

test('no stored promotions -> normal selection flow (no recovery)', async ({ page }) => {
  await page.route('**/api/promotions?scope=mine', (route) =>
    route.fulfill({ json: { promotions: [] } }),
  );
  await page.goto('/');
  // Nothing recovered: the empty review area, the selector is ready to drive a fresh request.
  await expect(page.getByText('PR de promoção aberto:')).toHaveCount(0);
  await expect(page.getByLabel('Recurso')).toBeVisible();
});

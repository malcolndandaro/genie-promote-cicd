import { test, expect } from '@playwright/test';

// LB3: on load the SPA recovers the most-recent promotion from its STORED snapshot — it must NOT
// re-run the LLM reviewer (no POST /api/promote). The live status still comes from the GitHub poll.

const review = {
  findings: [
    {
      rule_id: 'AUDIENCE-01',
      severity: 'BLOCKER',
      message: 'Grupo consumidor sem SELECT em prod_recebiveis.diamond.fato_recebiveis.',
      citation: 'CERC › Público do Space › AUDIENCE-01',
      suggestion: 'Conceder SELECT ao grupo consumidor.',
    },
  ],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada — 1 achado BLOCKER.' },
  eval: { status: 'pass', summary: '🟢 eval-run ok.' },
  allowlist_violations: [],
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
  requester_email: 'ana@databricks.com',
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

  const audit = [
    { seq: 1, event_type: 'requested', occurred_at: '2026-06-26T10:00:00Z',
      actor_github_login: null, actor_app_email: 'ana@databricks.com', github_event_at: null, detail: null },
    { seq: 2, event_type: 'pr_opened', occurred_at: '2026-06-26T10:00:05Z',
      actor_github_login: null, actor_app_email: null, github_event_at: null, detail: null },
    { seq: 3, event_type: 'merged', occurred_at: '2026-06-26T10:05:00Z',
      actor_github_login: 'PSPedro176', actor_app_email: null, github_event_at: '2026-06-26T10:05:00Z', detail: null },
  ];
  await page.route('**/api/promotions?scope=mine', (route) =>
    route.fulfill({ json: { promotions: [summary] } }),
  );
  await page.route(`**/api/promotions/${summary.id}`, (route) =>
    route.fulfill({ json: { promotion: summary, review, pr: PR, live_status: null, audit } }),
  );
  await page.route(`**/api/promotions/${summary.id}/audit`, (route) =>
    route.fulfill({ json: { audit } }),
  );

  await page.goto('/#/espacos');

  // The recovered promotion renders: PR banner + the stored finding + the live phase badge (polled).
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByText('AUDIENCE-01', { exact: true })).toBeVisible();
  await expect(page.getByText(/Promoção bloqueada/)).toBeVisible();
  await expect(page.getByText('Checagens em execução')).toBeVisible(); // live status resumed
  await expect(page.getByRole('link', { name: /Ver no GitHub/ })).toHaveAttribute('href', PR.url);

  // The audit trail (LB4): GitHub-attributed governance events. Scope to the audit section so the
  // assertions don't collide with the pipeline step of the same name ("Merge para main — pendente").
  await expect(page.getByText('Trilha de auditoria')).toBeVisible();
  const trail = page.locator('.audit');
  await expect(trail.getByText('Merge para main', { exact: true })).toBeVisible();
  await expect(trail.getByText('PSPedro176')).toBeVisible(); // authoritative GitHub identity

  // The clincher: the reviewer was never re-run.
  expect(promoteCalled).toBe(false);
});

test('no stored promotions -> normal selection flow (no recovery)', async ({ page }) => {
  await page.route('**/api/promotions?scope=mine', (route) =>
    route.fulfill({ json: { promotions: [] } }),
  );
  await page.goto('/#/espacos');
  // Nothing recovered: the empty review area, the space card is ready to drive a fresh request.
  await expect(page.getByText('PR de promoção aberto:')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeVisible();
});

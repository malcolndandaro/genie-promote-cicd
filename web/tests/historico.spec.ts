import { test, expect } from '@playwright/test';

// LB5: the "Minhas promoções" history screen + role-gated scope toggle (admins see all).

const review = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto.' },
  eval: { status: 'pass', summary: 'ok' },
  allowlist_violations: [],
  consumer_group: '',
  timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
};
const mine = [
  { id: 'p-mine', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'ana@databricks.com', pr_number: 6, pr_url: 'https://gh/pr/6',
    current_phase: 'open', terminal: false,
    created_at: '2026-06-26T10:00:00Z', updated_at: '2026-06-26T10:05:00Z' },
];
const all = [
  ...mine,
  { id: 'p-bob', resource_id: 'sp2', resource_kind: 'genie_space', resource_title: 'Cedentes',
    requester_email: 'bob@databricks.com', pr_number: 7, pr_url: 'https://gh/pr/7',
    current_phase: 'deployed', terminal: true,
    created_at: '2026-06-25T10:00:00Z', updated_at: '2026-06-25T10:05:00Z' },
];

function routes(page, { isAdmin }: { isAdmin: boolean }) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: isAdmin } }));
  page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: mine } }));
  page.route('**/api/promotions?scope=all', (r) => r.fulfill({ json: { promotions: all } }));
  page.route('**/api/promotions/p-mine', (r) =>
    r.fulfill({ json: { promotion: mine[0], review, pr: { number: 6, url: 'https://gh/pr/6' }, live_status: null, audit: [] } }));
  page.route('**/api/promote/6/status', (r) =>
    r.fulfill({ json: { pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
      deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null }, pr_url: 'https://gh/pr/6', phase: 'checks_running' } }));
}

test('a non-admin sees only their promotions; opening a row shows the stored review (no re-run)', async ({ page }) => {
  let promoteCalled = false;
  await page.route('**/api/promote', (r) => { promoteCalled = true; r.fulfill({ status: 500, json: {} }); });
  routes(page, { isAdmin: false });
  await page.goto('/');
  await page.getByRole('tab', { name: 'Minhas promoções' }).click();

  await expect(page.getByText('Recebíveis')).toBeVisible();
  await expect(page.getByText('Todas (Steward/Admin)')).toHaveCount(0); // no scope toggle for a non-admin

  await page.getByRole('button', { name: /Abrir/ }).click();
  // Switches to the review view and renders the STORED snapshot — no reviewer re-run.
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByText('🟢 Pronto.')).toBeVisible();
  expect(promoteCalled).toBe(false);
});

test('an admin gets the scope toggle and can list all promotions', async ({ page }) => {
  routes(page, { isAdmin: true });
  await page.goto('/');
  await page.getByRole('tab', { name: 'Minhas promoções' }).click();

  await expect(page.getByRole('button', { name: 'Todas (Steward/Admin)' })).toBeVisible();
  await page.getByRole('button', { name: 'Todas (Steward/Admin)' }).click();
  // The cross-user view shows another author's promotion too.
  await expect(page.getByText('Cedentes')).toBeVisible();
  await expect(page.getByText('Recebíveis')).toBeVisible();
});

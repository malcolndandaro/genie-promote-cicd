import { test, expect, type Page } from '@playwright/test';

// UI4: the business-user home — explainer + 3-step flow + "Meus espaços" cards + "Promoções
// recentes", the space-card promote that pre-selects the review flow, and the recent-promotion
// deep-link to #/promocoes/:id (recovered from the stored snapshot, no reviewer re-run).

const PR = { number: 6, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/6' };
const review = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
};
const summary = {
  id: 'promo-1', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
  requester_email: 'malcoln@databricks.com', pr_number: PR.number, pr_url: PR.url,
  current_phase: 'open', terminal: false,
  created_at: '2026-06-26T10:00:00Z', updated_at: '2026-06-26T10:05:00Z',
};
const status = {
  pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
  pr_url: PR.url, phase: 'checks_running',
};

function home(page: Page, promotions: unknown[] = [summary]) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }));
  page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }));
  page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions } }));
  page.route(`**/api/promotions/${summary.id}`, (r) =>
    r.fulfill({ json: { promotion: summary, review, pr: PR, live_status: status, audit: [] } }));
  page.route(`**/api/promote/${PR.number}/status`, (r) => r.fulfill({ json: status }));
}

test('renders the explainer, the 3-step flow, my spaces, and recent promotions', async ({ page }) => {
  home(page);
  await page.goto('/');

  await expect(page).toHaveURL(/#\/inicio$/);
  // Explainer + simplified governed flow.
  await expect(page.getByText('Promova seus Genie Spaces para produção, com governança')).toBeVisible();
  await expect(page.getByText('Autoria no dev', { exact: true })).toBeVisible();
  await expect(page.getByText('Revisão automatizada', { exact: true })).toBeVisible();
  await expect(page.getByText('Promoção governada', { exact: true })).toBeVisible();
  // My spaces (cards) + recent promotions.
  await expect(page.getByRole('heading', { name: 'Recebíveis', level: 3 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeVisible();
  await expect(page.getByText('Promoções recentes')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Abrir promoção: Recebíveis' })).toBeVisible();
});

test('empty states when the user has no spaces and no promotions', async ({ page }) => {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }));
  page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.goto('/');

  await expect(page.getByText('Nenhum Genie Space encontrado')).toBeVisible();
  await expect(page.getByText('Nenhuma promoção ainda')).toBeVisible();
});

test('a space card promote pre-selects the review flow on "Meus espaços"', async ({ page }) => {
  home(page, []); // no recent promotions, so recover() is a no-op
  await page.route('**/api/promote', (r) => r.fulfill({ json: { review, pr: { number: 99, url: PR.url }, promotion_id: 'new-1' } }));
  await page.goto('/');

  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  // Lands in the review flow on "Meus espaços", already promoting the chosen space.
  await expect(page).toHaveURL(/#\/espacos$/);
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
});

test('a recent promotion deep-links to its detail without re-running the reviewer', async ({ page }) => {
  let promoteCalled = false;
  await page.route('**/api/promote', (r) => { promoteCalled = true; r.fulfill({ status: 500, json: {} }); });
  home(page);
  await page.goto('/');

  await page.getByRole('button', { name: 'Abrir promoção: Recebíveis' }).click();
  // The shareable detail deep-link + the recovered review (from the stored snapshot).
  await expect(page).toHaveURL(/#\/promocoes\/promo-1$/);
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByText('🟢 Pronto para promoção.')).toBeVisible();
  await expect(page.getByRole('button', { name: '← Voltar para as promoções' })).toBeVisible();
  expect(promoteCalled).toBe(false);
});

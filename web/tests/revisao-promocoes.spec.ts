import { test, expect } from '@playwright/test';

// S6 (app-ux-overhaul, D2): the Steward's monitoring dashboard — a read-only, cross-space list of
// promotions awaiting review/deploy-gate action, linking out to GitHub. No new in-app approval.

const QUEUE = [
  { id: 'p1', resource_id: 's1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'ana@databricks.com', change_provider: 'github', external_id: '10',
    external_url: 'https://github.com/x/y/pull/10',
    current_phase: 'open', terminal: false,
    created_at: '2026-07-14T09:00:00Z', updated_at: '2026-07-14T09:05:00Z' },
];

function routes(page, { isSteward = true, isAdmin = false } = {}) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'pedro@databricks.com', steward: null, is_admin: isAdmin, is_steward: isSteward } }));
}

test('the page appears in the universal nav as "Aguardando ação no GitHub"', async ({ page }) => {
  routes(page);
  page.route('**/api/promotions?scope=steward-queue', (r) => r.fulfill({ json: { promotions: QUEUE } }));
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Aguardando ação no GitHub' })).toBeVisible();
});

test('the page is universally accessible in the nav, not persona-restricted', async ({ page }) => {
  routes(page, { isSteward: false, isAdmin: false });
  await page.goto('/');
  // "Aguardando ação no GitHub" appears for all users; per-user queue filtering happens server-side.
  await expect(page.getByRole('link', { name: 'Aguardando ação no GitHub' })).toBeVisible();
});

test('lists cross-user promotions with a GitHub link, no in-app approve action', async ({ page }) => {
  routes(page);
  page.route('**/api/promotions?scope=steward-queue', (r) => r.fulfill({ json: { promotions: QUEUE } }));
  await page.goto('/#/revisao');

  await expect(page.getByText('Recebíveis')).toBeVisible();
  await expect(page.getByText('solicitado por ana@databricks.com')).toBeVisible();
  const link = page.getByRole('link', { name: 'Ver no GitHub ↗' });
  await expect(link).toHaveAttribute('href', 'https://github.com/x/y/pull/10');
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(page.getByRole('button', { name: 'Aprovar' })).toHaveCount(0);
});

test('an empty queue shows a clear message', async ({ page }) => {
  routes(page);
  page.route('**/api/promotions?scope=steward-queue', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.goto('/#/revisao');
  await expect(page.getByText('Nenhuma promoção aguardando ação no momento.')).toBeVisible();
});

test('a 403 surfaces as a clear error', async ({ page }) => {
  routes(page);
  page.route('**/api/promotions?scope=steward-queue', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Apenas Stewards/Admins veem a fila de revisão.' } }));
  await page.goto('/#/revisao');
  await expect(page.getByRole('alert')).toBeVisible();
});

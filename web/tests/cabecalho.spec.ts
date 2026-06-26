import { test, expect } from '@playwright/test';

// UI2: the production top bar — GitHub repo link (config-driven URL) + identity-derived role badge.
function routes(
  page: import('@playwright/test').Page,
  who: Record<string, unknown>,
) {
  page.route('**/api/whoami', (r) => r.fulfill({ json: who }));
  page.route('**/api/spaces', (r) =>
    r.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
}

test('the GitHub icon links to the configured repo (new tab)', async ({ page }) => {
  routes(page, { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false,
                 repo_url: 'https://github.com/acme/genie' });
  await page.goto('/');
  const link = page.getByRole('link', { name: 'Abrir repositório no GitHub' });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', 'https://github.com/acme/genie');
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', /noopener/);
});

test('the GitHub link falls back to the default repo when whoami omits repo_url', async ({ page }) => {
  routes(page, { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false });
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Abrir repositório no GitHub' })).toHaveAttribute(
    'href',
    'https://github.com/malcolndandaro/genie-promote-cicd',
  );
});

test('a Steward (email == configured steward) sees the Steward badge', async ({ page }) => {
  routes(page, { email: 'pedro@databricks.com', steward: 'pedro@databricks.com', is_admin: true });
  await page.goto('/');
  await expect(page.getByRole('banner').getByText('Steward', { exact: true })).toBeVisible();
  await expect(page.getByRole('banner').getByText('Admin', { exact: true })).toHaveCount(0);
});

test('a pure Steward (email == steward, not admin) still sees the Steward badge', async ({ page }) => {
  routes(page, { email: 'pedro@databricks.com', steward: 'pedro@databricks.com', is_admin: false });
  await page.goto('/');
  await expect(page.getByRole('banner').getByText('Steward', { exact: true })).toBeVisible();
});

test('an Admin (not the steward) sees the Admin badge', async ({ page }) => {
  routes(page, { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: true });
  await page.goto('/');
  await expect(page.getByRole('banner').getByText('Admin', { exact: true })).toBeVisible();
});

test('a plain requester sees no role badge', async ({ page }) => {
  routes(page, { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false });
  await page.goto('/');
  await expect(page.getByRole('banner').getByText('Steward', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('banner').getByText('Admin', { exact: true })).toHaveCount(0);
  // ...but the identity is still shown.
  await expect(page.getByRole('banner').getByText('ana@databricks.com')).toBeVisible();
});

import { test, expect } from '@playwright/test';

// UI1: the production app shell — sidebar navigation, hash routing (deep-links + reload), the active
// state, and the mobile drawer. /api/* are mocked the same way the other smokes mock them.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }),
  );
  await page.route('**/api/spaces', (r) =>
    r.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  // Empty so recover-on-load is a no-op and the history screen shows its empty state.
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
});

test('lands on the default route with the sidebar nav and an active item', async ({ page }) => {
  await page.goto('/');
  // The four primary destinations are present as nav links.
  for (const label of ['Início', 'Meus espaços', 'Minhas promoções', 'Acesso']) {
    await expect(page.getByRole('link', { name: label })).toBeVisible();
  }
  // Default route is the home ("Início"), reflected in the URL and the active link.
  await expect(page).toHaveURL(/#\/inicio$/);
  await expect(page.getByRole('link', { name: 'Início' })).toHaveAttribute('aria-current', 'page');
});

test('sidebar navigation switches the screen AND the URL hash', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('link', { name: 'Início' }).click();
  await expect(page).toHaveURL(/#\/inicio$/);
  await expect(page.getByText('Autoria no dev')).toBeVisible(); // the home's 3-step flow
  await expect(page.getByRole('link', { name: 'Início' })).toHaveAttribute('aria-current', 'page');

  await page.getByRole('link', { name: 'Minhas promoções' }).click();
  await expect(page).toHaveURL(/#\/promocoes$/);
  await expect(page.getByText('Nenhuma promoção ainda')).toBeVisible();
});

test('a deep-linked hash lands on the right screen on load', async ({ page }) => {
  await page.goto('/#/promocoes');
  await expect(page.getByText('Nenhuma promoção ainda')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Minhas promoções' })).toHaveAttribute('aria-current', 'page');
});

test('an unknown hash falls back to the default screen', async ({ page }) => {
  await page.goto('/#/bogus');
  await expect(page).toHaveURL(/#\/inicio$/);
  await expect(page.getByRole('link', { name: 'Início' })).toHaveAttribute('aria-current', 'page');
});

test('on a narrow viewport the sidebar collapses into a toggle/drawer', async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 860 });
  await page.goto('/');

  // The hamburger is the way in on mobile; opening it reveals the scrim overlay.
  const menu = page.getByRole('button', { name: 'Abrir menu' });
  await expect(menu).toBeVisible();
  await expect(menu).toHaveAttribute('aria-expanded', 'false');

  await menu.click();
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('button', { name: 'Fechar menu' })).toBeVisible();

  // Escape closes the drawer.
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Fechar menu' })).toHaveCount(0);
  await expect(menu).toHaveAttribute('aria-expanded', 'false');

  // Reopen, then navigating from the drawer routes AND closes the drawer (scrim gone).
  await menu.click();
  await page.getByRole('link', { name: 'Minhas promoções' }).click();
  await expect(page).toHaveURL(/#\/promocoes$/);
  await expect(page.getByRole('button', { name: 'Fechar menu' })).toHaveCount(0);
});

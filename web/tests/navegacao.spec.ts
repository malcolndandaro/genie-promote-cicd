import { test, expect } from '@playwright/test';

// UI1: the production app shell — sidebar navigation, hash routing (deep-links + reload), the active
// state, and the mobile drawer. /api/* are mocked the same way the other smokes mock them.
//
// S2 (persona-sectioned nav): the sidebar is now grouped sections, not a flat list — these tests
// exercise the "Meu trabalho" section, which every caller holds regardless of persona.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }),
  );
  await page.route('**/api/spaces', (r) =>
    r.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  // Empty so recover-on-load is a no-op and the merged espaços page shows its empty-history state.
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
});

test('a Business User sees only Meus espaços and lands there', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('navigation').getByRole('link')).toHaveCount(1);
  await expect(page.getByRole('link', { name: 'Meus espaços' })).toBeVisible();
  await expect(page).toHaveURL(/#\/espacos$/);
  await expect(page.getByRole('link', { name: 'Meus espaços' })).toHaveAttribute('aria-current', 'page');
});

test('retired standalone hashes fall back to Meus espaços', async ({ page }) => {
  for (const id of ['inicio', 'acesso', 'aprovacoes', 'rehidratar', 'admin', 'assistente-conhecimento']) {
    await page.goto(`/#/${id}`);
    await expect(page).toHaveURL(/#\/espacos$/);
  }
});

test('a Steward+Admin capability union sees exactly the four pilot entries', async ({ page }) => {
  await page.unroute('**/api/whoami');
  await page.route('**/api/whoami', (r) => r.fulfill({ json: {
    email: 'pedro@databricks.com', steward: 'pedro@databricks.com',
    is_admin: true, is_steward: true,
  } }));
  await page.goto('/');
  const nav = page.getByRole('navigation');
  await expect(nav.getByRole('link')).toHaveCount(4);
  for (const label of ['Meus espaços', 'Aguardando minha revisão', 'Configurações', 'Auditoria']) {
    await expect(nav.getByRole('link', { name: label })).toBeVisible();
  }
});

test('a bare #/promocoes (no id) redirects to Meus espaços — there is no standalone list anymore', async ({ page }) => {
  await page.goto('/#/promocoes');
  await expect(page).toHaveURL(/#\/espacos$/);
  await expect(page.getByRole('link', { name: 'Meus espaços' })).toHaveAttribute('aria-current', 'page');
});

test('an unknown hash falls back to the default screen', async ({ page }) => {
  await page.goto('/#/bogus');
  await expect(page).toHaveURL(/#\/espacos$/);
  await expect(page.getByRole('link', { name: 'Meus espaços' })).toHaveAttribute('aria-current', 'page');
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
  await page.getByRole('link', { name: 'Meus espaços' }).click();
  await expect(page).toHaveURL(/#\/espacos$/);
  await expect(page.getByRole('button', { name: 'Fechar menu' })).toHaveCount(0);
});

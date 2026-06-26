import { test, expect } from '@playwright/test';

// UI5: accessibility + responsive + reduced-motion polish.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }));
  await page.route('**/api/spaces', (r) =>
    r.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }));
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
});

test('a skip-to-content link focuses the main region (keyboard a11y)', async ({ page }) => {
  await page.goto('/');
  const skip = page.getByRole('link', { name: 'Ir para o conteúdo' });
  await skip.focus();
  await expect(skip).toBeFocused();
  await skip.click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('conteudo');
});

test('navigating moves focus to the main content region', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: 'Minhas promoções' }).click();
  await expect(page).toHaveURL(/#\/promocoes$/);
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('conteudo');
});

test('the landmarks are present (nav + main)', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('navigation', { name: 'Navegação principal' })).toBeVisible();
  await expect(page.getByRole('main')).toBeVisible();
});

test('renders under prefers-reduced-motion on a phone viewport', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  // The home still renders its key sections, and the mobile chrome (hamburger) is present.
  await expect(page.getByText('Promova seus Genie Spaces para produção, com governança')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Abrir menu' })).toBeVisible();
});

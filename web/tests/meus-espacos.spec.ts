import { test, expect } from '@playwright/test';

// /whoami is needed on every page (header identity).
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
});

const oneSpace = (route: import('@playwright/test').Route) =>
  route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } });

test('lists the user spaces (OBO) as cards, each with a per-space promote action', async ({
  page,
}) => {
  await page.route('**/api/spaces', oneSpace);
  await page.goto('/');

  // App shell sidebar present.
  await expect(page.getByRole('link', { name: 'Meus espaços' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Novo Genie Space/ })).toBeVisible();

  // The space renders as a card: kind badge + title + a per-space "Solicitar promoção" button
  // (named with the space title so each card's action is distinct).
  await expect(page.getByRole('heading', { name: 'Recebíveis', level: 3 })).toBeVisible();
  await expect(page.getByText('Genie Space', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeEnabled();
});

test('while one space is promoting, the other space cards are disabled', async ({ page }) => {
  await page.route('**/api/spaces', (route) =>
    route.fulfill({
      json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }, { space_id: 'sp2', title: 'Cedentes' }] },
    }),
  );
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  // Gate the promote so the in-flight (reviewing) state stays observable.
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  await page.route('**/api/promote', async (route) => {
    await gate;
    await route.fulfill({
      json: {
        pr: { number: 1, url: 'https://gh/pr/1' },
        review: { findings: [], gate: { conclusion: 'success', blocker_count: 0, summary: 'ok' },
          eval: { status: 'advisory', summary: 'x' }, allowlist_violations: [], consumer_group: '', timeline: [] },
      },
    });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // The busy card spins; the OTHER space's card is disabled to prevent a concurrent request.
  await expect(page.getByRole('button', { name: 'Solicitando…' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Cedentes' })).toBeDisabled();
  release();
});

test('navigates to the "Novo Genie Space" screen and shows the placeholder', async ({ page }) => {
  await page.route('**/api/spaces', oneSpace);
  await page.goto('/');
  await page.getByRole('link', { name: /Novo Genie Space/ }).click();
  await expect(page.getByText('Em breve — assistente de criação', { exact: false })).toBeVisible();
});

test('shows the empty state with a next action when there are no spaces', async ({ page }) => {
  await page.route('**/api/spaces', (route) => route.fulfill({ json: { spaces: [] } }));
  await page.goto('/');

  await expect(page.getByText('Nenhum Genie Space encontrado')).toBeVisible();
  await expect(page.getByRole('button', { name: /Novo Genie Space/ })).toBeVisible();
});

test('shows an error state with retry when /api/spaces fails', async ({ page }) => {
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: list_spaces' } }),
  );
  await page.goto('/');

  await expect(page.getByText(/Não foi possível listar os espaços/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
});

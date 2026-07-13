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
  await page.goto('/#/espacos');

  // App shell sidebar present.
  await expect(page.getByRole('link', { name: 'Meus espaços' })).toBeVisible();

  // The space renders as a card: kind badge + title + a per-space "Solicitar promoção" button
  // (named with the space title so each card's action is distinct).
  await expect(page.getByRole('heading', { name: 'Recebíveis', level: 3 })).toBeVisible();
  await expect(page.getByText('Genie Space', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeEnabled();
});

// G3: choosing a card SELECTS the space and opens a confirmation panel bound to it — it does not
// fire the request. The grid locks (including the chosen card itself) until the panel is
// confirmed or cancelled, since the actual "Solicitar promoção" action now lives in the panel.
test('choosing a space locks the grid and opens a confirmation panel bound to it', async ({ page }) => {
  await page.route('**/api/spaces', (route) =>
    route.fulfill({
      json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }, { space_id: 'sp2', title: 'Cedentes' }] },
    }),
  );
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // The panel is scoped to `.confirm` since the (disabled) card behind it repeats the space title.
  const panel = page.locator('.confirm');
  await expect(panel.getByRole('heading', { name: 'Recebíveis', level: 3 })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Confirmar promoção' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Cedentes' })).toBeDisabled();

  // Cancelling releases the lock and drops the panel — no request was ever sent.
  await page.getByRole('button', { name: '← Escolher outro espaço' }).click();
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Cedentes' })).toBeEnabled();
});

test('confirming the panel shows a busy state while the promotion is in flight', async ({ page }) => {
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
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  // The confirm button goes busy; the OTHER space's card stays disabled (already was, on selection).
  await expect(page.getByRole('button', { name: 'Solicitando…' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Cedentes' })).toBeDisabled();
  release();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
});

test('shows the empty state pointing to the dev workspace when there are no spaces', async ({ page }) => {
  await page.route('**/api/spaces', (route) => route.fulfill({ json: { spaces: [] } }));
  await page.goto('/#/espacos');

  await expect(page.getByText('Nenhum Genie Space encontrado')).toBeVisible();
  await expect(page.getByText('Genie nativo do workspace de dev', { exact: false })).toBeVisible();
});

test('shows an error state with retry when /api/spaces fails', async ({ page }) => {
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: list_spaces' } }),
  );
  await page.goto('/#/espacos');

  await expect(page.getByText(/Não foi possível listar os espaços/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
});

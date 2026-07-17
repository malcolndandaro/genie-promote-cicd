import { test, expect } from '@playwright/test';
import { confirmPilotPromotion } from './promotion-helpers';

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
test('choosing and switching Spaces keeps one panel and resets it to the new Space', async ({ page }) => {
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
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Cedentes' })).toBeEnabled();

  // Directly switching selection remounts the working panel (no stale declaration can leak).
  await page.getByRole('button', { name: 'Solicitar promoção: Cedentes' }).click();
  await expect(panel.getByRole('heading', { name: 'Cedentes', level: 3 })).toBeVisible();
  await expect(page.getByLabel('Nome do space em produção')).toHaveValue('Cedentes');

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
          eval: { status: 'advisory', summary: 'x' }, allowlist_violations: [], timeline: [] },
      },
    });
  });
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  // The pipeline replaces the right-hand confirmation panel immediately; it never drops below the
  // full Space/history list or renders a second copy at the bottom of the page.
  const workingPanel = page.locator('.working-panel');
  await expect(workingPanel.getByText('Executando pipeline de promoção…')).toBeVisible();
  await expect(workingPanel.getByLabel('Pipeline de promoção')).toBeVisible();
  await expect(page.getByLabel('Pipeline de promoção')).toHaveCount(1);
  await expect(page.getByText('Acompanhe a decisão logo abaixo')).toHaveCount(0);

  // The confirm button goes busy; the OTHER space's card stays disabled (already was, on selection).
  await expect(page.getByRole('button', { name: 'Solicitando…' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitar promoção: Cedentes' })).toBeDisabled();
  release();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
});

test('each space card carries a dev env badge next to the kind badge', async ({ page }) => {
  await page.route('**/api/spaces', oneSpace);
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.goto('/#/espacos');

  const card = page.locator('.space-card').filter({ hasText: 'Recebíveis' });
  await expect(card.getByText('Genie Space', { exact: true })).toBeVisible();
  // the origin badge: these come from the dev authoring workspace
  await expect(card.getByText('dev', { exact: true })).toBeVisible();
});

test('a terminal (deployed) promotion is NOT auto-pinned as an inline pipeline on load', async ({ page }) => {
  // The reported bug: the last promotion's full pipeline showed at the bottom of the list on load,
  // even when browsing (nothing selected). recover() must only resume an IN-FLIGHT promotion.
  const PR = { number: 7, url: 'https://gh/pr/7' };
  const terminal = {
    id: 'promo-done', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'malcoln@databricks.com', pr_number: PR.number, pr_url: PR.url,
    current_phase: 'deployed', terminal: true, created_at: '2026-07-10T10:00:00Z',
  };
  await page.route('**/api/spaces', oneSpace);
  await page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: [terminal] } }));
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [terminal] } }));
  await page.goto('/#/espacos');

  // The space + its history summary render, but NO inline pipeline / PR banner is auto-shown.
  await expect(page.getByRole('heading', { name: 'Recebíveis', level: 3 })).toBeVisible();
  await expect(page.getByText('PR de promoção aberto:')).toHaveCount(0);
  await expect(page.getByText('Pipeline de promoção')).toHaveCount(0);
});

test('a no-op promotion (already in prod) shows a "nada a promover" notice, no pipeline', async ({ page }) => {
  await page.route('**/api/spaces', oneSpace);
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  // The backend detected the space is already in prod byte-identical: no PR, no_change: true.
  await page.route('**/api/promote', (route) =>
    route.fulfill({
      json: {
        pr: null, no_change: true,
        review: { findings: [], gate: { conclusion: 'success', blocker_count: 0, summary: 'ok' },
          eval: { status: 'advisory', summary: 'x' }, allowlist_violations: [], timeline: [] },
      },
    }),
  );
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  await expect(page.getByText('Nada a promover')).toBeVisible();
  // no PR banner, and the (misleading) pipeline is suppressed on a no-op.
  await expect(page.getByText('PR de promoção aberto:')).toHaveCount(0);
  await expect(page.getByText('Pipeline de promoção')).toHaveCount(0);
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

test('search and status filter narrow the Space list without losing context', async ({ page }) => {
  await page.route('**/api/spaces', (route) => route.fulfill({ json: { spaces: [
    { space_id: 'sp1', title: 'Recebíveis' }, { space_id: 'sp2', title: 'Arranjos' },
  ] } }));
  await page.route('**/api/promotions**', (route) => route.fulfill({ json: { promotions: [{
    id: 'p1', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'ana@databricks.com', current_phase: 'open', terminal: false,
    created_at: '2026-07-16T10:00:00Z', updated_at: '2026-07-16T10:00:00Z',
  }] } }));
  await page.goto('/');

  await page.getByRole('textbox', { name: 'Buscar Space' }).fill('arr');
  await expect(page.getByRole('heading', { name: 'Arranjos', level: 3 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Recebíveis', level: 3 })).toHaveCount(0);

  await page.getByRole('textbox', { name: 'Buscar Space' }).fill('');
  await page.getByRole('combobox', { name: 'Filtrar por status' }).selectOption('open');
  await expect(page.getByRole('heading', { name: 'Recebíveis', level: 3 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Arranjos', level: 3 })).toHaveCount(0);
});

test('direct Space switch clears an unsaved Público do Space', async ({ page }) => {
  await page.route('**/api/spaces', (route) => route.fulfill({ json: { spaces: [
    { space_id: 'sp1', title: 'Recebíveis' }, { space_id: 'sp2', title: 'Arranjos' },
  ] } }));
  await page.route('**/api/promotions**', (route) => route.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/principals**', (route) => route.fulfill({ json: { principals: [
    { type: 'group', id: 'g1', display: 'GestOps', email: null },
  ] } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /GestOps/ }).click();
  await expect(page.getByRole('button', { name: 'Confirmar promoção' })).toBeEnabled();

  await page.getByRole('button', { name: 'Solicitar promoção: Arranjos' }).click();
  await expect(page.getByLabel('Nome do space em produção')).toHaveValue('Arranjos');
  await expect(page.getByRole('button', { name: 'Confirmar promoção' })).toBeDisabled();
});

test('Prod to Dev export is contextual and only enabled after a deployed version exists', async ({ page }) => {
  const deployed = {
    id: 'p-live', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'ana@databricks.com', pr_number: 12, pr_url: 'https://gh/pr/12',
    current_phase: 'deployed', terminal: true,
    created_at: '2026-07-15T10:00:00Z', updated_at: '2026-07-15T11:00:00Z',
  };
  await page.route('**/api/spaces', oneSpace);
  await page.route('**/api/promotions**', (route) => route.fulfill({ json: { promotions: [deployed] } }));
  await page.route('**/api/promotions/p-live', (route) => route.fulfill({ json: {
    promotion: deployed, review: null, live_status: { phase: 'deployed' }, audit: [],
  } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  const exportButton = page.getByRole('button', { name: 'Exportar versão Prod para Dev' });
  await expect(exportButton).toBeEnabled();
  await exportButton.click();
  await expect(page).toHaveURL(/#\/promocoes\/p-live$/);
});

test('author journey has no document-level horizontal overflow on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/spaces', oneSpace);
  await page.route('**/api/promotions**', (route) => route.fulfill({ json: { promotions: [] } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await expect(page.getByText('Revisar e solicitar promoção', { exact: false })).toBeVisible();
  const sizes = await page.evaluate(() => ({ width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth }));
  expect(sizes.scrollWidth).toBeLessThanOrEqual(sizes.width);
});

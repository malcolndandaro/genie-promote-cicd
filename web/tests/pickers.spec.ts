import { test, expect } from '@playwright/test';

// G1 — happy path for the prefilled/searchable Picker: the F3 "Solicitar acesso" form no longer
// takes a typed Genie Space id; it must be PICKED from `/api/prod-spaces`. This exercises the
// Picker's open -> search -> select -> submit round trip end-to-end against the real Acesso screen.

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }),
  );
  await page.route('**/api/access-requests?scope=mine', (route) =>
    route.fulfill({ json: { requests: [] } }),
  );
});

test('picks a prod Space from the picker (no typed id) and submits an access request', async ({ page }) => {
  await page.route('**/api/prod-spaces**', (route) => {
    const url = new URL(route.request().url());
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const all = [
      { space_id: 'sp-receb', title: 'Recebíveis' },
      { space_id: 'sp-cedentes', title: 'Cedentes' },
    ];
    const spaces = q ? all.filter((s) => s.title.toLowerCase().includes(q)) : all;
    return route.fulfill({ json: { spaces } });
  });

  let posted: unknown;
  await page.route('**/api/access-requests', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    posted = route.request().postDataJSON();
    return route.fulfill({
      json: {
        request: {
          id: 'req-1', space_id: 'sp-receb', space_title: 'Recebíveis',
          requester_email: 'malcoln@databricks.com', note: null,
          want_space_permission: true, space_permission_level: 'CAN_RUN', want_uc_select: false,
          state: 'requested', decided_by: null, decided_at: null, decision_note: null,
          pr_number: null, pr_url: null, created_at: '2026-07-13T00:00:00Z', updated_at: '2026-07-13T00:00:00Z',
        },
      },
    });
  });

  await page.goto('/#/acesso');

  const picker = page.getByRole('combobox', { name: 'Genie Space' });
  await picker.click();
  await expect(page.getByRole('option', { name: /Recebíveis/ })).toBeVisible();
  await expect(page.getByRole('option', { name: /Cedentes/ })).toBeVisible();

  await picker.fill('receb');
  await expect(page.getByRole('option', { name: /Cedentes/ })).toHaveCount(0);
  await page.getByRole('option', { name: /Recebíveis/ }).click();

  // The picker now shows the PICKED space as a chip-like button — never leaves the raw id typed
  // anywhere in the DOM.
  await expect(page.getByRole('button', { name: /Recebíveis/ })).toBeVisible();

  await page.getByRole('button', { name: 'Enviar solicitação' }).click();

  await expect(page.getByText('Solicitação enviada')).toBeVisible();
  expect(posted).toMatchObject({ space_id: 'sp-receb', space_title: 'Recebíveis' });
});

test('shows the picker error state when the search fails', async ({ page }) => {
  await page.route('**/api/prod-spaces**', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: list_prod_spaces' } }),
  );

  await page.goto('/#/acesso');
  await page.getByRole('combobox', { name: 'Genie Space' }).click();

  await expect(page.getByText(/Erro ao buscar/)).toBeVisible();
});

import { test, expect } from '@playwright/test';

// The KA registry is embedded in the single Admin Configurações route. Covers registration,
// (serving endpoint picked from a live list, never typed), list + toggle + remove.

function routes(page, { isAdmin = true } = {}) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'admin@databricks.com', steward: null, is_admin: isAdmin } }));
  page.route('**/api/admin/serving-endpoints', (r) =>
    r.fulfill({ json: { endpoints: [{ name: 'ka-handbook' }, { name: 'databricks-claude-opus-4-8' }] } }));
  page.route('**/api/spaces', (r) =>
    r.fulfill({ json: { spaces: [{ space_id: 's1', title: 'Recebíveis' }] } }));
  page.route('**/api/admin/roles', (r) => r.fulfill({ json: { roles: [], bootstrap_env: { admins: [], stewards: [] } } }));
  page.route('**/api/admin/drift', (r) => r.fulfill({ json: { has_drift: false, has_unknown: false, findings: [] } }));
  page.route('**/api/admin/rules', (r) => r.fulfill({ json: { effective: [], overrides: [], hardcoded: [] } }));
  page.route('**/api/admin/prompt-template', (r) => r.fulfill({ json: { effective_text: 'padrão', default_text: 'padrão', custom: null } }));
}

test('there is no standalone KA entry; Configurações remains admin-only', async ({ page }) => {
  routes(page, { isAdmin: false });
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Assistente de Conhecimento' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Configurações' })).toHaveCount(0);
});

test('registers a GLOBAL endpoint, submitting the right body', async ({ page }) => {
  routes(page);
  page.route('**/api/admin/ka-endpoints', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { endpoints: [] } });
  });
  let posted: unknown;
  await page.route('**/api/admin/ka-endpoints', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    posted = route.request().postDataJSON();
    route.fulfill({ json: { endpoint: { id: 'e1', name: 'Handbook KA', serving_endpoint_name: 'ka-handbook',
      is_global: true, scope_space_ids: [], enabled: true, created_by: 'admin@x',
      created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z' } } });
  });
  await page.goto('/#/configuracoes');

  await page.getByLabel('Nome').fill('Handbook KA');
  await page.getByRole('combobox', { name: 'Serving endpoint' }).click();
  await page.getByRole('option', { name: 'ka-handbook' }).click();
  await page.getByRole('button', { name: 'Registrar' }).click();

  await expect.poll(() => posted).toEqual({
    name: 'Handbook KA', serving_endpoint_name: 'ka-handbook',
    is_global: true, scope_space_ids: [], enabled: true,
  });
});

test('registering a SCOPED endpoint requires at least one space', async ({ page }) => {
  routes(page);
  page.route('**/api/admin/ka-endpoints', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: { endpoints: [] } }) : route.fallback());
  await page.goto('/#/configuracoes');

  await page.getByLabel('Nome').fill('Scoped KA');
  await page.getByRole('combobox', { name: 'Serving endpoint' }).click();
  await page.getByRole('option', { name: 'ka-handbook' }).click();
  await page.getByRole('radio', { name: 'Espaços específicos' }).check();

  const submit = page.getByRole('button', { name: 'Registrar' });
  await expect(submit).toBeDisabled(); // no space picked yet

  await page.getByRole('combobox', { name: 'Espaços' }).click();
  await page.getByRole('option', { name: 'Recebíveis' }).click();
  await expect(submit).toBeEnabled();
});

test('lists registered endpoints with scope + enabled state, and can toggle/remove', async ({ page }) => {
  routes(page);
  const endpoint = { id: 'e1', name: 'Handbook KA', serving_endpoint_name: 'ka-handbook',
    is_global: true, scope_space_ids: [], enabled: true, created_by: 'admin@x',
    created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z' };
  let current = endpoint;
  await page.route('**/api/admin/ka-endpoints', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: { endpoints: [current] } });
    return route.fallback();
  });
  await page.route('**/api/admin/ka-endpoints/e1', (route) => {
    current = { ...current, enabled: false };
    route.fulfill({ json: { endpoint: current } });
  });
  await page.route('**/api/admin/ka-endpoints/e1/delete', (route) => route.fulfill({ json: { ok: true } }));

  await page.goto('/#/configuracoes');
  await expect(page.getByText('Handbook KA')).toBeVisible();
  await expect(page.getByText('Todos os espaços', { exact: true })).toBeVisible();
  await expect(page.getByText('Ativo', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Desativar' }).click();
  await expect(page.getByText('Desativado')).toBeVisible();

  await page.getByRole('button', { name: 'Remover' }).click();
});

test('an empty registry shows a clear message', async ({ page }) => {
  routes(page);
  page.route('**/api/admin/ka-endpoints', (route) =>
    route.request().method() === 'GET' ? route.fulfill({ json: { endpoints: [] } }) : route.fallback());
  await page.goto('/#/configuracoes');
  await expect(page.getByText('Nenhum endpoint registrado ainda.')).toBeVisible();
});

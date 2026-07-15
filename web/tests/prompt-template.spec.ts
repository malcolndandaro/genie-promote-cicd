import { test, expect } from '@playwright/test';

// S8 (app-ux-overhaul) — Admin → Configurações → "Prompt do revisor": the reviewer's persona/policy
// text, admin-editable in-app (Lakebase-backed on the server; here the API is mocked). The
// PROTECTED_CORE (prompt-injection defense + JSON output schema) is appended server-side and is NOT
// editable. Covers: showing the default when nothing's saved, saving a valid template (right body),
// a server 400 surfacing inline (not a crash), and resetting to default.

const DEFAULT_PERSONA = 'Você é o revisor padrão de Genie Spaces. Avalie o espaço conforme o handbook.';

function mockSettingsScreen(
  page: import('@playwright/test').Page,
  opts: { custom?: { template_text: string; updated_by: string; updated_at: string } | null } = {},
) {
  let custom = opts.custom ?? null;

  // Common Settings-screen mocks (mirrors regras.spec.ts — the screen loads roles/drift/rules too).
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'admin@databricks.com', steward: 'pedro@databricks.com', is_admin: true } }),
  );
  page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  page.route('**/api/admin/roles', (r) =>
    r.fulfill({ json: { roles: [], bootstrap_env: { admins: ['admin@databricks.com'], stewards: [] } } }),
  );
  page.route('**/api/admin/drift', (r) => r.fulfill({ json: { has_drift: false, has_unknown: false, findings: [] } }));
  page.route('**/api/admin/rules', (r) => r.fulfill({ json: { effective: [], overrides: [], hardcoded: [] } }));

  page.route('**/api/admin/prompt-template', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: { custom, default: DEFAULT_PERSONA } });
    }
    const body = route.request().postDataJSON() as { template_text: string };
    custom = {
      template_text: body.template_text,
      updated_by: 'admin@databricks.com',
      updated_at: '2026-07-14T00:00:00Z',
    };
    return route.fulfill({ json: { custom } });
  });
  page.route('**/api/admin/prompt-template/reset', (route) => {
    custom = null;
    return route.fulfill({ json: { ok: true } });
  });
}

function promptCard(page: import('@playwright/test').Page) {
  return page.locator('.card').filter({ hasText: 'O texto de persona/política' });
}

test('shows the hardcoded default when no custom template is saved', async ({ page }) => {
  mockSettingsScreen(page);
  await page.goto('/#/configuracoes');

  await expect(page.getByRole('heading', { name: 'Prompt do revisor' })).toBeVisible();
  const textarea = promptCard(page).getByRole('textbox');
  await expect(textarea).toHaveValue(DEFAULT_PERSONA);
  await expect(promptCard(page).getByText('editando a partir do padrão do sistema')).toBeVisible();
  // The protected-core note is always shown.
  await expect(promptCard(page).getByText('núcleo protegido')).toBeVisible();
});

test('a saved custom template pre-fills the editor', async ({ page }) => {
  mockSettingsScreen(page, {
    custom: { template_text: 'Persona customizada', updated_by: 'admin@databricks.com', updated_at: '2026-07-14T00:00:00Z' },
  });
  await page.goto('/#/configuracoes');

  await expect(promptCard(page).getByRole('textbox')).toHaveValue('Persona customizada');
  await expect(promptCard(page).getByText('Template customizado salvo por admin@databricks.com')).toBeVisible();
});

test('saving a valid template POSTs the edited text and round-trips as custom', async ({ page }) => {
  mockSettingsScreen(page);
  let posted: unknown;
  await page.route('**/api/admin/prompt-template', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    posted = route.request().postDataJSON();
    // Fall through to mockSettingsScreen's POST handler (records the custom row + fulfills), so the
    // subsequent reload's GET reflects the saved state.
    return route.fallback();
  });

  await page.goto('/#/configuracoes');
  const textarea = promptCard(page).getByRole('textbox');
  await textarea.fill('Nova persona de revisor');
  await promptCard(page).getByRole('button', { name: 'Salvar' }).click();

  await expect.poll(() => posted).toEqual({ template_text: 'Nova persona de revisor' });
  // Reloads → the saved custom state is reflected (its "salvo por" note + reset button appear).
  await expect(promptCard(page).getByText('Template customizado salvo por admin@databricks.com')).toBeVisible();
  await expect(promptCard(page).getByRole('button', { name: 'Restaurar padrão' })).toBeVisible();
});

test('a 400 from save surfaces the server message inline, not a crash', async ({ page }) => {
  mockSettingsScreen(page);
  await page.route('**/api/admin/prompt-template', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({
      status: 400,
      json: { detail: 'Template inválido: a revisão de teste não produziu JSON válido.' },
    });
  });

  await page.goto('/#/configuracoes');
  await promptCard(page).getByRole('textbox').fill('template quebrado');
  await promptCard(page).getByRole('button', { name: 'Salvar' }).click();

  await expect(
    promptCard(page).getByText('Template inválido: a revisão de teste não produziu JSON válido.'),
  ).toBeVisible();
  // The screen is still alive — the editor and its content survived.
  await expect(promptCard(page).getByRole('textbox')).toHaveValue('template quebrado');
});

test('resetting to default calls the reset endpoint and clears the custom state', async ({ page }) => {
  mockSettingsScreen(page, {
    custom: { template_text: 'Persona customizada', updated_by: 'admin@databricks.com', updated_at: '2026-07-14T00:00:00Z' },
  });
  let resetCalled = false;
  await page.route('**/api/admin/prompt-template/reset', (route) => {
    resetCalled = true;
    // Fall through to mockSettingsScreen's handler, which nulls the custom state + fulfills.
    return route.fallback();
  });

  await page.goto('/#/configuracoes');
  await expect(promptCard(page).getByText('Template customizado salvo por')).toBeVisible();
  await promptCard(page).getByRole('button', { name: 'Restaurar padrão' }).click();

  await expect.poll(() => resetCalled).toBe(true);
  // Reloads → back to the default (no custom), so the reset button is gone and the default note shows.
  await expect(promptCard(page).getByText('editando a partir do padrão do sistema')).toBeVisible();
  await expect(promptCard(page).getByRole('button', { name: 'Restaurar padrão' })).toHaveCount(0);
});

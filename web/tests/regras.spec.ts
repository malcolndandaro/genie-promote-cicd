import { test, expect } from '@playwright/test';

// G2 — Admin → Configurações → "Regras": the reviewer's handbook rules, admin-configurable
// (Lakebase-backed on the server; here the API is mocked). Covers: listing the hardcoded rules +
// their override state, disabling a rule, editing EVAL-01's threshold, resetting to default, and
// adding/removing a custom rule. The server is the real merge/gating authority — these tests only
// verify the UI round-trips through `/api/admin/rules` correctly.

const HARDCODED = [
  { rule_id: 'ENV-01', severity_hint: 'BLOCKER', citation: 'Handbook › ENV-01', content: 'catálogo por ambiente' },
  { rule_id: 'GRANT-01', severity_hint: 'BLOCKER', citation: 'Handbook › GRANT-01', content: 'grupo consumidor tem SELECT' },
  { rule_id: 'EVAL-01', severity_hint: 'BLOCKER', citation: 'Handbook › EVAL-01', content: '>= N benchmarks',
    params: { min_benchmarks: 2 } },
  { rule_id: 'SQL-01', severity_hint: 'STYLE', citation: 'Handbook › SQL-01', content: 'convenções SQL' },
];

function mockAdminScreen(page: import('@playwright/test').Page, overridesInit: Record<string, unknown>[] = []) {
  let overrides = overridesInit;

  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'admin@databricks.com', steward: 'pedro@databricks.com', is_admin: true } }),
  );
  page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  page.route('**/api/admin/roles', (r) =>
    r.fulfill({ json: { roles: [], bootstrap_env: { admins: ['admin@databricks.com'], stewards: [] } } }),
  );
  page.route('**/api/admin/drift', (r) => r.fulfill({ json: { has_drift: false, has_unknown: false, findings: [] } }));

  function effective(): Record<string, unknown>[] {
    const byId = new Map(overrides.map((o) => [o.rule_id, o]));
    const out: Record<string, unknown>[] = [];
    for (const rule of HARDCODED) {
      const ov = byId.get(rule.rule_id) as Record<string, unknown> | undefined;
      if (ov && ov.enabled === false) continue;
      out.push(ov ? { ...rule, severity_hint: ov.severity ?? rule.severity_hint,
        params: ov.params ?? rule.params, content: ov.content ?? rule.content } : rule);
    }
    for (const ov of overrides) {
      if (ov.is_custom && ov.enabled !== false) {
        out.push({ rule_id: ov.rule_id, severity_hint: ov.severity, citation: ov.citation, content: ov.content });
      }
    }
    return out;
  }

  page.route('**/api/admin/rules', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: { effective: effective(), overrides, hardcoded: HARDCODED } });
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    const now = new Date().toISOString();
    const row = {
      rule_id: body.rule_id, is_custom: body.is_custom ?? false, enabled: body.enabled ?? true,
      severity: body.severity ?? null, params: body.params ?? null, content: body.content ?? null,
      citation: body.citation ?? null, updated_by: 'admin@databricks.com', updated_at: now,
    };
    overrides = [...overrides.filter((o) => o.rule_id !== row.rule_id), row];
    return route.fulfill({ json: { rule: row } });
  });
  page.route('**/api/admin/rules/reset', (route) => {
    const body = route.request().postDataJSON() as { rule_id: string };
    overrides = overrides.filter((o) => o.rule_id !== body.rule_id);
    return route.fulfill({ json: { ok: true } });
  });
}

function rulesCard(page: import('@playwright/test').Page) {
  return page.locator('.card').filter({ hasText: 'As regras do handbook' });
}

test('lists the hardcoded rules with severity badge and citation', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');
  await expect(page.getByRole('heading', { name: 'Regras', exact: true })).toBeVisible();
  await expect(page.getByText('ENV-01', { exact: true })).toBeVisible();
  await expect(page.getByText('Handbook › ENV-01')).toBeVisible();
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
  await expect(page.getByText('SQL-01', { exact: true })).toBeVisible();
});

test('disabling a rule persists and shows as disabled', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');

  const sqlRow = page.locator('li').filter({ hasText: 'SQL-01' });
  await sqlRow.getByRole('checkbox').uncheck();
  await expect(sqlRow.getByText('desabilitada')).toBeVisible();

  // Survives a refresh (round-tripped through the mocked store, not just local state).
  await rulesCard(page).getByRole('button', { name: 'Atualizar' }).click();
  await expect(page.locator('li').filter({ hasText: 'SQL-01' }).getByText('desabilitada')).toBeVisible();
});

test('editing EVAL-01\'s minimum benchmarks threshold persists', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');

  const evalRow = page.locator('li').filter({ hasText: 'EVAL-01' });
  const input = evalRow.getByLabel('Mín. benchmarks');
  await expect(input).toHaveValue('2');
  await input.fill('5');
  await input.blur();

  await rulesCard(page).getByRole('button', { name: 'Atualizar' }).click();
  await expect(page.locator('li').filter({ hasText: 'EVAL-01' }).getByLabel('Mín. benchmarks')).toHaveValue('5');
});

test('editing EVAL-01\'s eval-run threshold (%) persists as a fraction, alongside min_benchmarks', async ({
  page,
}) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');

  const evalRow = page.locator('li').filter({ hasText: 'EVAL-01' });
  const thresholdInput = evalRow.getByLabel('Limiar do eval-run (%)');
  await expect(thresholdInput).toHaveValue('80'); // default 0.8 -> 80%
  await thresholdInput.fill('60');
  await thresholdInput.blur();

  // Survives a refresh, AND doesn't wipe out min_benchmarks (same params jsonb, must be merged).
  await rulesCard(page).getByRole('button', { name: 'Atualizar' }).click();
  const refreshedRow = page.locator('li').filter({ hasText: 'EVAL-01' });
  await expect(refreshedRow.getByLabel('Limiar do eval-run (%)')).toHaveValue('60');
  await expect(refreshedRow.getByLabel('Mín. benchmarks')).toHaveValue('2');

  // Editing min_benchmarks afterwards must likewise NOT wipe out the threshold just set.
  await refreshedRow.getByLabel('Mín. benchmarks').fill('4');
  await refreshedRow.getByLabel('Mín. benchmarks').blur();
  await rulesCard(page).getByRole('button', { name: 'Atualizar' }).click();
  const finalRow = page.locator('li').filter({ hasText: 'EVAL-01' });
  await expect(finalRow.getByLabel('Mín. benchmarks')).toHaveValue('4');
  await expect(finalRow.getByLabel('Limiar do eval-run (%)')).toHaveValue('60');
});

test('a configurable rule with an override can be reset back to default', async ({ page }) => {
  // SQL-01 is a configurable (non-pipeline-locked) rule, so it has a reset affordance;
  // ENV-01/GRANT-01 are locked and never show one.
  mockAdminScreen(page, [
    { rule_id: 'SQL-01', is_custom: false, enabled: false, severity: null, params: null,
      content: null, citation: null, updated_by: 'admin@databricks.com', updated_at: '2026-07-01T00:00:00Z' },
  ]);
  await page.goto('/#/configuracoes');

  const sqlRow = page.locator('li').filter({ hasText: 'SQL-01' });
  await expect(sqlRow.getByText('desabilitada')).toBeVisible();
  await sqlRow.getByRole('button', { name: 'Restaurar padrão' }).click();

  await expect(page.locator('li').filter({ hasText: 'SQL-01' }).getByText('desabilitada')).toHaveCount(0);
  // The reset override disappears, so "Restaurar padrão" itself is gone too.
  await expect(page.locator('li').filter({ hasText: 'SQL-01' }).getByRole('button', { name: 'Restaurar padrão' })).toHaveCount(0);
});

test('adding a custom rule appears in the list and can be removed', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');

  await page.getByLabel('rule_id').fill('CUSTOM-01');
  await page.getByLabel('Texto/instrução (PT)').fill('nunca usar SELECT *');
  await page.getByLabel('Citação').fill('Padrão interno de SQL');
  await page.getByRole('button', { name: 'Adicionar regra custom' }).click();

  const customRow = page.locator('li').filter({ hasText: 'CUSTOM-01' });
  await expect(customRow.getByText('nunca usar SELECT *')).toBeVisible();
  await expect(customRow.getByText('Custom', { exact: true })).toBeVisible();

  await customRow.getByRole('button', { name: 'Remover' }).click();
  await expect(page.locator('li').filter({ hasText: 'CUSTOM-01' })).toHaveCount(0);
});

test('a custom rule can be DISABLED (kept) separately from being removed', async ({ page }) => {
  // "Desabilitar" (toggle) and "Remover" (delete) are distinct actions on a custom rule: disabling
  // keeps the row but drops it from the reviewer prompt; the toggle must re-send the full custom
  // payload (isCustom+severity+content+citation) or the backend rejects it.
  mockAdminScreen(page, [
    { rule_id: 'CUSTOM-9', is_custom: true, enabled: true, severity: 'SUGGESTION', params: null,
      content: 'evite abreviações', citation: 'Padrão interno', updated_by: 'admin@databricks.com',
      updated_at: '2026-07-16T00:00:00Z' },
  ]);
  await page.goto('/#/configuracoes');

  const row = page.locator('li').filter({ hasText: 'CUSTOM-9' });
  await expect(row.getByRole('checkbox')).toBeChecked();
  await row.getByRole('checkbox').uncheck();
  await expect(row.getByText('desabilitada')).toBeVisible();

  // Survives a refresh (round-tripped through the mocked store) AND the row is still there (not removed).
  await rulesCard(page).getByRole('button', { name: 'Atualizar' }).click();
  const refreshed = page.locator('li').filter({ hasText: 'CUSTOM-9' });
  await expect(refreshed.getByText('desabilitada')).toBeVisible();
  await expect(refreshed.getByRole('button', { name: 'Remover' })).toBeVisible();  // remove still available
});

test('groups rules into deterministic + configurable sections', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');
  await expect(page.getByRole('heading', { name: 'Verificações determinísticas' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Regras configuráveis (revisor)' })).toBeVisible();
});

test('shows each configurable rule\'s editable handbook text', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');
  // A configurable rule shows its handbook text + an "Editar texto" affordance.
  const sqlRow = page.locator('li').filter({ hasText: 'SQL-01' });
  await expect(sqlRow.getByText('convenções SQL')).toBeVisible();
  await expect(sqlRow.getByRole('button', { name: 'Editar texto' })).toBeVisible();
});

test('editing a configurable rule\'s text persists as an override', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');

  const sqlRow = page.locator('li').filter({ hasText: 'SQL-01' });
  await sqlRow.getByRole('button', { name: 'Editar texto' }).click();
  const textarea = sqlRow.getByRole('textbox');
  await textarea.fill('sempre alias com AS, nunca SELECT *');
  await sqlRow.getByRole('button', { name: 'Salvar texto' }).click();

  // Survives a refresh (round-tripped through the mocked store).
  await rulesCard(page).getByRole('button', { name: 'Atualizar' }).click();
  const refreshed = page.locator('li').filter({ hasText: 'SQL-01' });
  await expect(refreshed.getByText('sempre alias com AS, nunca SELECT *')).toBeVisible();
  await expect(refreshed.getByText('Editado por admin@databricks.com')).toBeVisible();
});

test('pipeline-locked rules (ENV-01/GRANT-01) are read-only: no toggle, no edit, informative text', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');

  const grantRow = page.locator('li').filter({ hasText: 'GRANT-01' });
  await expect(grantRow.getByText('sempre ativa no pipeline')).toBeVisible();
  // read-only: no enable checkbox, no "Editar texto", no severity dropdown
  await expect(grantRow.getByRole('checkbox')).toHaveCount(0);
  await expect(grantRow.getByRole('button', { name: 'Editar texto' })).toHaveCount(0);
  await expect(grantRow.getByRole('combobox')).toHaveCount(0);
  // it DOES inform what the check does
  await expect(grantRow.getByText(/check_grants/)).toBeVisible();
});

test('EVAL-01 is the configurable deterministic rule: keeps its params + toggle, text read-only', async ({ page }) => {
  mockAdminScreen(page);
  await page.goto('/#/configuracoes');
  const evalRow = page.locator('li').filter({ hasText: 'EVAL-01' });
  await expect(evalRow.getByText('determinística · configurável')).toBeVisible();
  // configurable knobs remain
  await expect(evalRow.getByLabel('Mín. benchmarks')).toHaveValue('2');
  await expect(evalRow.getByLabel('Limiar do eval-run (%)')).toHaveValue('80');
  await expect(evalRow.getByRole('checkbox')).toBeVisible();
  // but its TEXT is not editable (deterministic check, informative only)
  await expect(evalRow.getByRole('button', { name: 'Editar texto' })).toHaveCount(0);
});

test('a 403 from the server surfaces as a clear error, not a silent empty screen', async ({ page }) => {
  mockAdminScreen(page);
  await page.unroute('**/api/admin/rules');
  await page.route('**/api/admin/rules', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Apenas Stewards/Admins acessam o console de administração.' } }),
  );
  await page.goto('/#/configuracoes');
  await expect(page.getByText('Sem permissão — esta tela é restrita a Admins.')).toBeVisible();
});

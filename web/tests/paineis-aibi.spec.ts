import { test, expect } from '@playwright/test';

// The AI/BI dashboard promotion flow through the SAME screens as a Genie Space — the whole point of
// the kind seam is that only the KIND-VARYING bits differ. Backend mocked via route fulfillment
// (no live workspace), like every other spec here.

const DASH = { id: 'd_dash1', title: 'Painel de Recebíveis', kind: 'dashboard', env: 'dev' };
const SPACE = { id: 'sp1', title: 'Recebíveis', kind: 'genie_space', env: 'dev' };

/** A dashboard review: NO eval-run node, a `structure` node instead. */
const dashboardReview = {
  findings: [
    {
      rule_id: 'DASH-02',
      severity: 'SUGGESTION',
      message: "o dataset 'ds_orfao' não é usado por nenhum widget (consulta morta).",
      citation: 'Genie Promotion Handbook › Dashboards › DASH-02',
      suggestion: 'Remova o dataset não utilizado, ou adicione um widget que o use.',
    },
  ],
  gate: { conclusion: 'neutral', blocker_count: 0, summary: '🟡 1 achado assessor — não bloqueia.' },
  eval: { status: 'advisory', summary: 'Painel AI/BI não tem perguntas de benchmark.' },
  allowlist_violations: [],
  audience_spec: { principals: [{ principal: 'users', is_group: true }] },
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' },
    { key: 'structure', label: 'Checagens do painel (datasets, widgets, páginas)', status: 'pass' },
    { key: 'approval', label: 'Revisão do Responsável Técnico (GitHub)', status: 'running' },
    { key: 'deploy', label: 'Deploy em produção (service principal)', status: 'pending' },
  ],
};

const blockedReview = {
  ...dashboardReview,
  findings: [
    {
      rule_id: 'DASH-01',
      severity: 'BLOCKER',
      message: "o widget 'w_bar' referencia o dataset 'ds_gone', que não existe na definição do painel.",
      citation: 'Genie Promotion Handbook › Dashboards › DASH-01',
      suggestion: 'Corrija a consulta do widget no painel em dev e promova novamente.',
    },
  ],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 1 de 1 achado(s) são BLOCKER — promoção bloqueada.' },
  timeline: dashboardReview.timeline.map((s) =>
    s.key === 'structure' ? { ...s, status: 'fail' } : s,
  ),
};

const PR = { number: 99, url: 'https://github.com/o/r/pull/99' };

async function baseRoutes(page, { resources = [SPACE, DASH] } = {}) {
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', is_admin: false, prod_host: 'https://prod.example.com' } }),
  );
  await page.route('**/api/resources', (r) => r.fulfill({ json: { resources } }));
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/promote/preview**', (r) =>
    r.fulfill({
      json: {
        title: 'Painel de Recebíveis',
        tables: [
          {
            source: 'dev_recebiveis.diamond.fato_recebiveis',
            default_target: 'prod_recebiveis.diamond.fato_recebiveis',
          },
        ],
      },
    }),
  );
  await page.route('**/api/principals**', (r) =>
    r.fulfill({
      json: { principals: [{ type: 'group', id: 'g1', display: 'users', email: null }] },
    }),
  );
}

/** Pick the audience and confirm — the shared confirm step, for a dashboard. */
async function confirmDashboard(page) {
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  const option = page.getByRole('option', { name: /users/ });
  await expect(option).toBeVisible();
  await option.evaluate((el) => (el as HTMLElement).click());
  await page.getByRole('button', { name: /Confirmar promoção/ }).click();
}

test('a dashboard appears in the same list as a Genie Space, badged by kind', async ({ page }) => {
  await baseRoutes(page);
  await page.goto('/#/espacos');

  await expect(page.getByText('Painel de Recebíveis')).toBeVisible();
  await expect(page.getByText('Recebíveis', { exact: true })).toBeVisible();
  // The kind badge is what tells the author what they are about to promote.
  await expect(page.getByText('Painel AI/BI').first()).toBeVisible();
  await expect(page.getByText('Genie Space').first()).toBeVisible();
  await expect(page.getByText('2 recursos disponíveis em Dev')).toBeVisible();
});

test('selecting a dashboard shows the confirm step with the dashboard audience level', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();

  // The declared audience derives CAN_READ for a dashboard (CAN_RUN is Genie's level).
  await expect(page.getByText('Público do Painel AI/BI')).toBeVisible();
  await expect(page.getByText('CAN_READ').first()).toBeVisible();
  await expect(page.getByText('CAN_RUN')).toHaveCount(0);
});

test('the de-para offers only real dataset tables, never a markdown hostname', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();

  await expect(page.getByText('1 referência')).toBeVisible();
  await page.getByText('Opções avançadas de de-para').click();
  await expect(page.getByText('dev_recebiveis.diamond.fato_recebiveis')).toBeVisible();
  await expect(page.getByText(/wikipedia|en\./)).toHaveCount(0);
});

test('promoting a dashboard sends its kind and opens a draft PR', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  let posted: Record<string, unknown> | null = null;
  await page.route('**/api/promote', (r) => {
    posted = r.request().postDataJSON();
    return r.fulfill({ json: { review: dashboardReview, pr: PR, promotion_id: 'p-dash' } });
  });
  await page.route('**/api/promote/99/status', (r) =>
    r.fulfill({
      json: {
        pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
        deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
        pr_url: PR.url, phase: 'open',
      },
    }),
  );
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();
  await confirmDashboard(page);

  await expect(page.getByText(/Rascunho pronto:/).first()).toBeVisible();
  expect(posted).toMatchObject({ resource_id: 'd_dash1', resource_kind: 'dashboard' });
});

test('a dashboard pipeline shows the dashboard checks and NO eval-run step', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.route('**/api/promote', (r) =>
    r.fulfill({ json: { review: dashboardReview, pr: PR, promotion_id: 'p-dash' } }),
  );
  await page.route('**/api/promote/99/status', (r) =>
    r.fulfill({
      json: {
        pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
        deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
        pr_url: PR.url, phase: 'open',
      },
    }),
  );
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();
  await confirmDashboard(page);

  await expect(page.getByText('Checagens do painel (datasets, widgets, páginas)')).toBeVisible();
  // The benchmark eval panel must not render for a kind that has no benchmarks.
  await expect(page.getByText('Eval-run')).toHaveCount(0);
  await expect(page.getByText(/perguntas de benchmark\?/)).toHaveCount(0);
});

test('a structural BLOCKER blocks the dashboard promotion and is shown to the author', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.route('**/api/promote', (r) =>
    r.fulfill({ json: { review: blockedReview, pr: PR, promotion_id: 'p-dash', blocked: true } }),
  );
  await page.route('**/api/promote/99/status', (r) =>
    r.fulfill({
      json: {
        pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
        deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
        pr_url: PR.url, phase: 'open',
      },
    }),
  );
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();
  await confirmDashboard(page);

  await expect(page.getByText('DASH-01').first()).toBeVisible();
  await expect(page.getByText(/não existe na definição do painel/)).toBeVisible();
  // The draft PR still opens (so the checks run as a dry-run) — that is the draft-first contract.
  await expect(page.getByText(/Rascunho pronto:/).first()).toBeVisible();
});

test('a deployed dashboard links to its PUBLISHED view in prod', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.route('**/api/promote', (r) =>
    r.fulfill({ json: { review: dashboardReview, pr: PR, promotion_id: 'p-dash' } }),
  );
  await page.route('**/api/promote/99/status', (r) =>
    r.fulfill({
      json: {
        pr_state: 'open', merged: true, checks: 'success', review_decision: 'approved',
        deploy: { status: 'completed', conclusion: 'success', waiting_approval: false, run_url: 'https://gh/run/1' },
        pr_url: PR.url, phase: 'deployed',
        prod_resource_id: 'prod-dash-1', prod_resource_kind: 'dashboard',
      },
    }),
  );
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();
  await confirmDashboard(page);

  const link = page.getByRole('link', { name: /Abrir Painel AI\/BI em produção/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute(
    'href',
    'https://prod.example.com/dashboardsv3/prod-dash-1/published',
  );
});

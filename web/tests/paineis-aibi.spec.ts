import { test, expect } from '@playwright/test';

// The DEDICATED "Painéis AI/BI" page (`#/paineis`).
//
// Dashboards used to share "Meus espaços" with Genie Spaces, badged by kind. They now have their own
// destination, because the two resources are authored, described and reviewed differently: a painel is
// datasets/widgets/pages filed under a business area, and none of that fits Genie's vocabulary.
// Backend mocked via route fulfillment (no live workspace), like every other spec here.

const DASH = { id: 'd_dash1', title: 'Painel de Recebíveis', kind: 'dashboard', env: 'dev' };
const SPACE = { id: 'sp1', title: 'Recebíveis', kind: 'genie_space', env: 'dev' };

const AREAS = [
  { key: 'risco', label: 'Risco' },
  { key: 'compliance', label: 'Compliance' },
];

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

const STATUS_OPEN = {
  pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
  pr_url: PR.url, phase: 'open',
};

async function baseRoutes(page, { resources = [SPACE, DASH] } = {}) {
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', is_admin: false, prod_host: 'https://prod.example.com' } }),
  );
  await page.route('**/api/resources', (r) => r.fulfill({ json: { resources } }));
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/business-areas', (r) => r.fulfill({ json: { areas: AREAS } }));
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
        structure: {
          datasets: ['ds_volume_bandeira', 'ds_top_cedentes'],
          n_widgets: 4,
          pages: ['Recebíveis — visão geral'],
        },
      },
    }),
  );
  await page.route('**/api/principals**', (r) =>
    r.fulfill({
      json: { principals: [{ type: 'group', id: 'g1', display: 'users', email: null }] },
    }),
  );
}

/** Select the painel, then fill BOTH required declarations (audience + area) and confirm. */
async function confirmPainel(page, { area = 'risco' } = {}) {
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  const option = page.getByRole('option', { name: /users/ });
  await expect(option).toBeVisible();
  await option.evaluate((el) => (el as HTMLElement).click());
  await page.getByRole('combobox', { name: 'Área de negócio' }).selectOption(area);
  await page.getByRole('button', { name: /Confirmar promoção/ }).click();
}

test('the painéis page lists ONLY dashboards, never a Genie Space', async ({ page }) => {
  await baseRoutes(page);
  await page.goto('/#/paineis');

  await expect(page.getByRole('heading', { name: 'Promover um painel AI/BI' })).toBeVisible();
  await expect(page.getByText('Painel de Recebíveis')).toBeVisible();
  // The Genie Space in the same /api/resources payload must NOT appear here.
  await expect(page.getByText('Recebíveis', { exact: true })).toHaveCount(0);
  await expect(page.getByText('1 painel disponível em Dev')).toBeVisible();
});

test('"Meus espaços" lists ONLY Genie Spaces, never a painel', async ({ page }) => {
  await baseRoutes(page);
  await page.goto('/#/espacos');

  await expect(page.getByText('Recebíveis', { exact: true })).toBeVisible();
  await expect(page.getByText('Painel de Recebíveis')).toHaveCount(0);
  await expect(page.getByText('1 Spaces disponíveis em Dev')).toBeVisible();
});

test('the nav offers Painéis AI/BI as its own destination', async ({ page }) => {
  await baseRoutes(page);
  await page.goto('/#/espacos');

  const link = page.getByRole('link', { name: 'Painéis AI/BI' });
  await expect(link).toBeVisible();
  await link.click();
  await expect(page.getByRole('heading', { name: 'Promover um painel AI/BI' })).toBeVisible();
});

test('the confirm step summarises the painel structure it read from the definition', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.goto('/#/paineis');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();

  // Dashboard-native: what IS this painel, before promoting it.
  await expect(page.getByText('datasets', { exact: true })).toBeVisible();
  await expect(page.getByText('widgets', { exact: true })).toBeVisible();
  await expect(page.getByText('ds_volume_bandeira · ds_top_cedentes')).toBeVisible();
});

test('the audience derives CAN_READ, the dashboard level', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.goto('/#/paineis');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();

  await expect(page.getByText('Público do Painel AI/BI')).toBeVisible();
  await expect(page.getByText('CAN_READ').first()).toBeVisible();
  await expect(page.getByText('CAN_RUN')).toHaveCount(0);
});

test('the area picker is a controlled list and previews the exact repository path', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.goto('/#/paineis');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();

  const picker = page.getByRole('combobox', { name: 'Área de negócio' });
  await expect(picker).toBeVisible();
  // A closed set — exactly the configured areas, plus the disabled prompt. No free text.
  await expect(picker.locator('option')).toHaveCount(AREAS.length + 1);

  await picker.selectOption('risco');
  // The path is derived from the area + the production title, shown BEFORE committing.
  await expect(page.getByText('src/dashboards/risco/painel_de_recebiveis/')).toBeVisible();
});

test('confirming is blocked until BOTH the audience and the area are declared', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.goto('/#/paineis');
  await page.getByRole('button', { name: /Preparar promoção: Painel de Recebíveis/ }).click();

  const confirm = page.getByRole('button', { name: /Confirmar promoção/ });
  await expect(confirm).toBeDisabled();
  await expect(page.getByText(/escolha o público e a área de negócio/i)).toBeVisible();

  // Audience alone is not enough — the area decides WHERE the painel is filed.
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  const option = page.getByRole('option', { name: /users/ });
  await expect(option).toBeVisible();
  await option.evaluate((el) => (el as HTMLElement).click());
  await expect(confirm).toBeDisabled();

  await page.getByRole('combobox', { name: 'Área de negócio' }).selectOption('risco');
  await expect(confirm).toBeEnabled();
});

test('promoting a painel sends its kind AND its area', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  let posted: Record<string, unknown> | null = null;
  await page.route('**/api/promote', (r) => {
    posted = r.request().postDataJSON();
    return r.fulfill({ json: { review: dashboardReview, pr: PR, promotion_id: 'p-dash' } });
  });
  await page.route('**/api/promote/99/status', (r) => r.fulfill({ json: STATUS_OPEN }));
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/paineis');
  await confirmPainel(page, { area: 'compliance' });

  await expect(page.getByText(/Rascunho pronto:/).first()).toBeVisible();
  expect(posted).toMatchObject({
    resource_id: 'd_dash1',
    resource_kind: 'dashboard',
    area: 'compliance',
  });
});

test('the pipeline shows the painel checks and NO eval-run step', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.route('**/api/promote', (r) =>
    r.fulfill({ json: { review: dashboardReview, pr: PR, promotion_id: 'p-dash' } }),
  );
  await page.route('**/api/promote/99/status', (r) => r.fulfill({ json: STATUS_OPEN }));
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/paineis');
  await confirmPainel(page);

  await expect(page.getByText('Checagens do painel (datasets, widgets, páginas)')).toBeVisible();
  // A painel has no benchmarks, so an eval-run node would name a check that can never run.
  await expect(page.getByText('Eval-run')).toHaveCount(0);
  await expect(page.getByText(/perguntas de benchmark\?/)).toHaveCount(0);
});

test('a structural BLOCKER is shown and the draft PR still opens', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.route('**/api/promote', (r) =>
    r.fulfill({ json: { review: blockedReview, pr: PR, promotion_id: 'p-dash', blocked: true } }),
  );
  await page.route('**/api/promote/99/status', (r) => r.fulfill({ json: STATUS_OPEN }));
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/paineis');
  await confirmPainel(page);

  await expect(page.getByText('DASH-01').first()).toBeVisible();
  await expect(page.getByText(/não existe na definição do painel/)).toBeVisible();
  // Draft-first: the PR opens anyway so the checks run as a dry-run.
  await expect(page.getByText(/Rascunho pronto:/).first()).toBeVisible();
});

test('a deployed painel links to its PUBLISHED view in prod', async ({ page }) => {
  await baseRoutes(page, { resources: [DASH] });
  await page.route('**/api/promote', (r) =>
    r.fulfill({ json: { review: dashboardReview, pr: PR, promotion_id: 'p-dash' } }),
  );
  await page.route('**/api/promote/99/status', (r) =>
    r.fulfill({
      json: {
        ...STATUS_OPEN,
        merged: true, checks: 'success', review_decision: 'approved',
        deploy: { status: 'completed', conclusion: 'success', waiting_approval: false, run_url: 'https://gh/run/1' },
        phase: 'deployed',
        prod_resource_id: 'prod-dash-1', prod_resource_kind: 'dashboard',
      },
    }),
  );
  await page.route('**/api/promotions/p-dash/audit', (r) => r.fulfill({ json: { audit: [] } }));

  await page.goto('/#/paineis');
  await confirmPainel(page);

  const link = page.getByRole('link', { name: /Abrir Painel AI\/BI em produção/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute(
    'href',
    'https://prod.example.com/dashboardsv3/prod-dash-1/published',
  );
});

test('an empty painel list points at the dev workspace', async ({ page }) => {
  await baseRoutes(page, { resources: [SPACE] }); // only a Genie Space exists
  await page.goto('/#/paineis');

  await expect(page.getByText('Nenhum painel AI/BI encontrado')).toBeVisible();
  await expect(page.getByText(/lixeira não são promovíveis/)).toBeVisible();
});

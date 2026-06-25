import { test, expect } from '@playwright/test';

// /whoami is needed on every page (header identity).
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
});

const oneSpace = (route: import('@playwright/test').Route) =>
  route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } });

test('lists the user spaces (OBO) and gates the request button until one is selected', async ({
  page,
}) => {
  await page.route('**/api/spaces', oneSpace);
  await page.goto('/');

  // Tabs shell present.
  await expect(page.getByRole('tab', { name: 'Meus espaços' })).toBeVisible();
  await expect(page.getByRole('tab', { name: /Novo Genie Space/ })).toBeVisible();

  // The space is listed as a select option.
  await expect(page.getByRole('option', { name: 'Recebíveis' })).toBeAttached();

  // The request button is disabled until a space is selected.
  const btn = page.getByRole('button', { name: /Solicitar promoção/ });
  await expect(btn).toBeDisabled();

  // Selecting the space enables it and shows the resource note (kind badge + title).
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await expect(btn).toBeEnabled();
  await expect(page.getByText('Genie Space', { exact: true })).toBeVisible();
});

test('switches to the "Novo Genie Space" tab and shows the placeholder', async ({ page }) => {
  await page.route('**/api/spaces', oneSpace);
  await page.goto('/');
  await page.getByRole('tab', { name: /Novo Genie Space/ }).click();
  await expect(page.getByText('Em breve — assistente de criação', { exact: false })).toBeVisible();
});

test('requesting a review renders the (placeholder) verdict summary', async ({ page }) => {
  await page.route('**/api/spaces', oneSpace);
  await page.route('**/api/review', (route) =>
    route.fulfill({
      json: {
        findings: [{ rule_id: 'EVAL-01', severity: 'BLOCKER', message: 'Poucas perguntas' }],
        gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada' },
        eval: { status: 'advisory', summary: '🟡 eval indisponível' },
        allowlist_violations: [],
        consumer_group: 'account users',
        timeline: [{ key: 'checks', label: 'Checagens', status: 'pass' }],
      },
    }),
  );
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();

  await expect(page.getByText('🔴 Promoção bloqueada')).toBeVisible();
  await expect(page.getByText('1 achado(s)', { exact: false })).toBeVisible();
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

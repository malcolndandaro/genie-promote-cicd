import { test, expect } from '@playwright/test';

// Pilot contract: Público do Space is required, uses one user/group picker, always derives
// CAN_RUN, and travels on the promotion request as AudienceSpec. There is no UC grant picker.

const cleanReview = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
};
const PR = { number: 7, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/7' };
const principals = [
  { type: 'user', id: 'u1', display: 'Ana Souza', email: 'ana@databricks.com' },
  { type: 'group', id: 'g1', display: 'users', email: null },
];

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  await page.route('**/api/promotions**', (route) => route.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/principals**', (route) => {
    const query = new URL(route.request().url()).searchParams.get('q')?.toLowerCase() ?? '';
    return route.fulfill({
      json: {
        principals: query
          ? principals.filter((principal) => principal.display.toLowerCase().includes(query))
          : principals,
      },
    });
  });
});

test('requires Público do Space and sends canonical AudienceSpec', async ({ page }) => {
  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  const confirm = page.getByRole('button', { name: 'Confirmar promoção' });
  await expect(page.getByText('2. Público do Space')).toBeVisible();
  await expect(page.getByText('obrigatório')).toBeVisible();
  await expect(confirm).toBeDisabled();
  await expect(page.getByText('Selecione ao menos uma pessoa ou grupo para continuar.')).toBeVisible();

  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /Ana Souza/ }).click();
  await expect(confirm).toBeEnabled();
  await confirm.click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toMatchObject({
    space_id: 'sp1',
    audience_spec: {
      principals: [{ principal: 'ana@databricks.com', is_group: false }],
    },
  });
});

test('offers one ACL audience flow, including workspace-local groups, with fixed CAN_RUN', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.locator('.audience__level')).toHaveText('CAN_RUN');
  await expect(page.getByText(/A promoção não concede SELECT/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Adicionar principal \(dados\)/ })).toHaveCount(0);
  await expect(page.getByText(/Acesso aos dados \(UC SELECT\)/)).toHaveCount(0);

  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await expect(page.getByRole('option', { name: /users/ })).toBeVisible();
});

test('re-requesting the same Space cannot reuse a stale audience', async ({ page }) => {
  const posted: Record<string, unknown>[] = [];
  await page.route('**/api/promote', (route) => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /Ana Souza/ }).click();
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();

  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  const confirm = page.getByRole('button', { name: 'Confirmar promoção' });
  await expect(confirm).toBeDisabled();
  const picker = page.getByRole('combobox', { name: 'Usuário ou grupo' });
  await picker.click();
  const users = page.getByRole('option', { name: /users/ });
  await expect(users).toBeVisible();
  // The fixed picker panel can extend below Playwright's synthetic viewport after the first
  // review; dispatch the same DOM click after proving the option is visible.
  await users.evaluate((element) => (element as HTMLElement).click());
  await expect(confirm).toBeEnabled();
  await confirm.click();

  expect(posted).toHaveLength(2);
  expect(posted[0]).toHaveProperty('audience_spec.principals.0.principal', 'ana@databricks.com');
  expect(posted[1]).toHaveProperty('audience_spec.principals.0.principal', 'users');
});

test('cancelling the confirmation panel discards the selected audience', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /Ana Souza/ }).click();
  await expect(page.getByRole('button', { name: 'Confirmar promoção' })).toBeEnabled();

  await page.getByRole('button', { name: '← Escolher outro espaço' }).click();
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await expect(page.getByRole('button', { name: 'Confirmar promoção' })).toBeDisabled();
});

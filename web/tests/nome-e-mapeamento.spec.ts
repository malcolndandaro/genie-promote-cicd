import { test, expect } from '@playwright/test';

// G7: the promotion-time counterpart to G6's rehydrate de-para — "PromotionConfirm" gains an
// editable prod Space name (pre-filled with the dev title) + a per-table de-para, loaded from
// `/api/promote/preview` BEFORE the caller confirms. Both are ONLY declarations (the app never
// applies anything prod-direct): they ride the SAME `/api/promote` request access_spec always did.

const cleanReview = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
};
const PR = { number: 9, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/9' };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/promote/preview**', (route) =>
    route.fulfill({
      json: {
        title: 'Recebíveis',
        tables: [{
          source: 'dev_recebiveis.diamond.fato_recebiveis',
          default_target: 'prod_recebiveis.diamond.fato_recebiveis',
        }],
      },
    }),
  );
});

test('the prod Space name is pre-filled with the dev title and editable', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  const nameInput = page.getByLabel('Nome do space em produção');
  await expect(nameInput).toHaveValue('Recebíveis');
  await nameInput.fill('Recebíveis PROD');

  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toMatchObject({ space_id: 'sp1', resource_title: 'Recebíveis PROD' });
});

test('confirming without editing the name sends the dev title unchanged', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toMatchObject({ resource_title: 'Recebíveis' });
  expect(posted).not.toHaveProperty('table_mapping');
});

test('shows the de-para pre-filled with the default target and sends only the OVERRIDDEN row', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.getByText('De-para de tabelas')).toBeVisible();
  await expect(page.getByText('dev_recebiveis.diamond.fato_recebiveis')).toBeVisible();
  const targetInput = page.getByLabel('Tabela em produção para dev_recebiveis.diamond.fato_recebiveis');
  await expect(targetInput).toHaveValue('prod_recebiveis.diamond.fato_recebiveis');
  // "Restaurar padrão" is hidden while the row still matches the default.
  await expect(page.getByRole('button', { name: 'Restaurar padrão' })).toHaveCount(0);

  await targetInput.fill('prod_recebiveis.diamond.fato_recebiveis_v2');
  await expect(page.getByRole('button', { name: 'Restaurar padrão' })).toBeVisible();

  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted?.table_mapping).toEqual({
    'dev_recebiveis.diamond.fato_recebiveis': 'prod_recebiveis.diamond.fato_recebiveis_v2',
  });
});

test('"Restaurar padrão" resets an edited row back to the default target', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  const targetInput = page.getByLabel('Tabela em produção para dev_recebiveis.diamond.fato_recebiveis');
  await targetInput.fill('prod_recebiveis.diamond.custom');
  await page.getByRole('button', { name: 'Restaurar padrão' }).click();

  await expect(targetInput).toHaveValue('prod_recebiveis.diamond.fato_recebiveis');
  await expect(page.getByRole('button', { name: 'Restaurar padrão' })).toHaveCount(0);
});

test('a preview load failure degrades gracefully — the name still defaults and confirm still works', async ({ page }) => {
  await page.route('**/api/promote/preview**', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: promote_preview' } }),
  );

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // The name still pre-fills synchronously from the already-known dev title — no network wait.
  await expect(page.getByLabel('Nome do space em produção')).toHaveValue('Recebíveis');
  await expect(page.getByText('Não foi possível carregar o de-para de tabelas')).toBeVisible();

  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toMatchObject({ resource_title: 'Recebíveis' });
  expect(posted).not.toHaveProperty('table_mapping'); // no override to send — degrades to plain rebind
});

test('re-requesting the SAME space after a completed promotion resets the name/mapping form', async ({ page }) => {
  // G9 (found live, PR #25 — same class of bug as the AccessSpec case): re-selecting the SAME
  // space after a completed promotion (the grid re-enables once reviewed) must NOT carry the
  // PRIOR round's edited name/table-mapping into the next request.
  const posted: Record<string, unknown>[] = [];
  await page.route('**/api/promote', (route) => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  const nameInput = page.getByLabel('Nome do space em produção');
  await nameInput.fill('Recebíveis PROD');
  const targetInput = page.getByLabel('Tabela em produção para dev_recebiveis.diamond.fato_recebiveis');
  await targetInput.fill('prod_recebiveis.diamond.fato_recebiveis_v2');
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({ resource_title: 'Recebíveis PROD' });
  expect(posted[0]?.table_mapping).toBeTruthy();

  // Re-select the SAME space without editing anything — the form must come back pre-filled with
  // the PLAIN dev title/default mapping (fresh preview), not the prior round's edit.
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await expect(page.getByLabel('Nome do space em produção')).toHaveValue('Recebíveis');
  await expect(page.getByRole('button', { name: 'Restaurar padrão' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();

  expect(posted).toHaveLength(2);
  expect(posted[1]).toMatchObject({ resource_title: 'Recebíveis' });
  expect(posted[1]).not.toHaveProperty('table_mapping'); // the stale override did NOT ride along
});

test('cancelling the confirmation panel discards any declared name/mapping', async ({ page }) => {
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  const nameInput = page.getByLabel('Nome do space em produção');
  await nameInput.fill('Nome temporário');
  const targetInput = page.getByLabel('Tabela em produção para dev_recebiveis.diamond.fato_recebiveis');
  await targetInput.fill('prod_recebiveis.diamond.custom');

  await page.getByRole('button', { name: '← Escolher outro espaço' }).click();

  // Re-choosing the same space starts clean — the previously edited name/mapping are gone.
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await expect(page.getByLabel('Nome do space em produção')).toHaveValue('Recebíveis');
  await expect(page.getByRole('button', { name: 'Restaurar padrão' })).toHaveCount(0);
});

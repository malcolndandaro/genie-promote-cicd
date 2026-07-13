import { test, expect } from '@playwright/test';

// G3: "Declarar acesso (opcional)" is no longer a loose page-level block — it lives INSIDE the
// confirmation panel bound to the CHOSEN space (escolher → declarar acesso opcional → confirmar).
// This covers both paths end-to-end: confirming with nothing declared (the one-click skip) and
// confirming with an access declaration, asserting the SAME `/api/promote` payload contract as
// before (access_spec rides the request exactly as F2 always sent it).

const cleanReview = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
};
const PR = { number: 7, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/7' };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  await page.route('**/api/promotions**', (r) => r.fulfill({ json: { promotions: [] } }));
});

test('confirming without declaring access sends no access_spec (one-click skip)', async ({ page }) => {
  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // The toggle stays collapsed — skipping the declaration is a single click straight to confirm.
  await expect(
    page.getByRole('button', { name: 'Quem deve usar este espaço em produção? (opcional)' }),
  ).toHaveAttribute('aria-expanded', 'false');
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toMatchObject({ space_id: 'sp1' });
  expect(posted).not.toHaveProperty('access_spec');
});

test('confirming WITH a declared access sends the same access_spec contract as before', async ({ page }) => {
  await page.route('**/api/principals**', (route) => {
    const url = new URL(route.request().url());
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    const all = [
      { type: 'user', id: 'u1', display: 'Ana Souza', email: 'ana@databricks.com' },
      { type: 'group', id: 'g1', display: 'analistas-recebiveis', email: null },
    ];
    const principals = q ? all.filter((p) => p.display.toLowerCase().includes(q)) : all;
    return route.fulfill({ json: { principals } });
  });
  let posted: Record<string, unknown> | undefined;
  await page.route('**/api/promote', (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // Open the declaration step (contextualized under the chosen space) and pick a Space principal.
  await page.getByRole('button', { name: 'Quem deve usar este espaço em produção? (opcional)' }).click();
  await page.getByRole('button', { name: '＋ Adicionar principal (espaço)' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /Ana Souza/ }).click();

  await page.getByRole('button', { name: 'Confirmar promoção' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toMatchObject({
    space_id: 'sp1',
    access_spec: {
      space_permissions: [{ principal: 'ana@databricks.com', is_group: false, level: 'CAN_RUN' }],
      uc_principals: [],
    },
  });
});

test('the UC-data picker excludes a workspace-local group; the Space picker still offers it', async ({ page }) => {
  // G9: "users" is a workspace-LOCAL built-in group (uc_grantable: false) — UC grants reject it
  // outright. It must never appear in the "Acesso aos dados (UC SELECT)" picker, but the Genie
  // Space-permissions picker (workspace ACLs accept local groups) keeps offering it.
  await page.route('**/api/principals**', (route) =>
    route.fulfill({
      json: {
        principals: [
          { type: 'group', id: 'g-users', display: 'users', email: null, uc_grantable: false },
          { type: 'group', id: 'g-acct', display: 'grp_genie_receivables', email: null, uc_grantable: true },
        ],
      },
    }),
  );

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('button', { name: 'Quem deve usar este espaço em produção? (opcional)' }).click();

  await page.getByRole('button', { name: '＋ Adicionar principal (dados)' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await expect(page.getByRole('option', { name: /grp_genie_receivables/ })).toBeVisible();
  await expect(page.getByRole('option', { name: /users/ })).toHaveCount(0);
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: '＋ Adicionar principal (espaço)' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).first().click();
  await expect(page.getByRole('option', { name: /users/ })).toBeVisible();
});

test('re-requesting the SAME space after a completed promotion does not resend a stale access_spec', async ({ page }) => {
  // G9 (found live, PR #25): a re-request of the SAME space silently carried the PREVIOUS
  // AccessSpec — select() never cleared pendingAccessSpec, and the confirm panel keyed off
  // resource id never remounted for a same-space reselect. The grid re-enables once a promotion is
  // "reviewed" (an open PR), so re-selecting the same card without going through "cancel" first is
  // the exact live path.
  await page.route('**/api/principals**', (route) =>
    route.fulfill({ json: { principals: [{ type: 'user', id: 'u1', display: 'Ana Souza', email: 'ana@databricks.com', uc_grantable: true }] } }),
  );
  const posted: Record<string, unknown>[] = [];
  await page.route('**/api/promote', (route) => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({ json: { review: cleanReview, pr: PR } });
  });

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // Round 1: declare access, then confirm.
  await page.getByRole('button', { name: 'Quem deve usar este espaço em produção? (opcional)' }).click();
  await page.getByRole('button', { name: '＋ Adicionar principal (espaço)' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /Ana Souza/ }).click();
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  expect(posted).toHaveLength(1);
  expect(posted[0]).toHaveProperty('access_spec');

  // Round 2: re-select the SAME space (the grid re-enables once reviewed) and confirm WITHOUT
  // declaring anything again — the form must come back empty, and the request must carry nothing.
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('button', { name: 'Quem deve usar este espaço em produção? (opcional)' }).click();
  await expect(page.getByRole('button', { name: 'Remover linha' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();

  expect(posted).toHaveLength(2);
  expect(posted[1]).not.toHaveProperty('access_spec'); // the stale declaration did NOT ride along
});

test('cancelling the confirmation panel discards any declared access', async ({ page }) => {
  await page.route('**/api/principals**', (route) =>
    route.fulfill({ json: { principals: [{ type: 'user', id: 'u1', display: 'Ana Souza', email: 'ana@databricks.com' }] } }),
  );
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review: cleanReview, pr: PR } }));

  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('button', { name: 'Quem deve usar este espaço em produção? (opcional)' }).click();
  await page.getByRole('button', { name: '＋ Adicionar principal (espaço)' }).click();
  await page.getByRole('combobox', { name: 'Usuário ou grupo' }).click();
  await page.getByRole('option', { name: /Ana Souza/ }).click();

  await page.getByRole('button', { name: '← Escolher outro espaço' }).click();

  // Re-choosing the same space starts clean — the previously declared row is gone.
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await expect(page.getByRole('button', { name: 'Remover linha' })).toHaveCount(0);
});

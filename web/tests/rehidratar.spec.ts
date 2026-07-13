import { test, expect } from '@playwright/test';

// G5 — "Exportar para dev" (prod->dev rehydrate) has TWO entry points:
//  1. the standalone "Trazer de volta para o dev" screen (sidebar), which picks a prod Space from
//     `getProdSpaces` (never a typed id) — covers the general case;
//  2. `RehydrateAction` on a Space's OWN deployed promotion detail — covers the "I just deployed
//     this, now let me keep iterating on it in dev" case.
// Both delegate the actual create/overwrite/confirm/run state machine to the same `RehydrateFlow`.

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({
      json: {
        email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false,
        dev_host: 'dev.cloud.databricks.com',
      },
    }),
  );
});

// --- 1. the standalone screen ---------------------------------------------------------------

test.describe('standalone "Trazer de volta para o dev" screen', () => {
  test.beforeEach(async ({ page }) => {
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
  });

  test('picks a prod Space (no typed id), creates a new dev Space, and links to it', async ({ page }) => {
    let posted: Record<string, unknown> | undefined;
    await page.route('**/api/rehydrate', (route) => {
      posted = route.request().postDataJSON();
      return route.fulfill({ json: { space_id: 'dev-new-1', mode: 'create', title: 'Recebíveis (dev)' } });
    });

    await page.goto('/#/rehidratar');
    await expect(page.getByRole('heading', { name: 'Trazer de volta para o dev', level: 2 })).toBeVisible();

    await page.getByRole('combobox', { name: 'Genie Space em produção' }).click();
    await page.getByRole('option', { name: /Recebíveis/ }).click();

    // "Criar um novo Space no dev" is the default choice — straight to confirm.
    await page.getByRole('button', { name: 'Continuar' }).click();
    await page.getByRole('button', { name: 'Confirmar rehidratação' }).click();

    await expect(page.getByText('Space criado no dev:')).toBeVisible();
    await expect(page.getByText('dev-new-1')).toBeVisible();
    const link = page.getByRole('link', { name: 'Abrir no dev ↗' });
    await expect(link).toHaveAttribute('href', 'https://dev.cloud.databricks.com/genie/rooms/dev-new-1');

    // Never sends a promotion_id — the engine resolves it server-side from the source id (G5).
    expect(posted).toMatchObject({ source_prod_space_id: 'sp-receb', mode: 'create' });
    expect(posted?.promotion_id).toBeFalsy();
  });

  test('overwrites an existing dev Space picked from the caller\'s own list', async ({ page }) => {
    await page.route('**/api/spaces', (route) =>
      route.fulfill({ json: { spaces: [{ space_id: 'dev-1', title: 'Recebíveis (dev)' }] } }),
    );
    let posted: Record<string, unknown> | undefined;
    await page.route('**/api/rehydrate', (route) => {
      posted = route.request().postDataJSON();
      return route.fulfill({ json: { space_id: 'dev-1', mode: 'overwrite', title: null } });
    });

    await page.goto('/#/rehidratar');
    await page.getByRole('combobox', { name: 'Genie Space em produção' }).click();
    await page.getByRole('option', { name: /Recebíveis/ }).click();

    await page.getByRole('radio', { name: 'Sobrescrever um Space existente no dev' }).check();
    await page.getByRole('combobox', { name: 'Space de dev a sobrescrever' }).click();
    await page.getByRole('option', { name: /Recebíveis \(dev\)/ }).click();

    await page.getByRole('button', { name: 'Continuar' }).click();
    await expect(page.getByText('vai sobrescrever')).toBeVisible();
    await page.getByRole('button', { name: 'Confirmar rehidratação' }).click();

    await expect(page.getByText('Space atualizado no dev:')).toBeVisible();
    expect(posted).toMatchObject({ source_prod_space_id: 'sp-receb', mode: 'overwrite', dev_space_id: 'dev-1' });
  });

  test('the fail-closed guard denial shows a clear "no access" message, not a generic error', async ({ page }) => {
    await page.route('**/api/rehydrate', (route) =>
      route.fulfill({ status: 403, json: { detail: 'malcoln@databricks.com may not access space sp-receb' } }),
    );

    await page.goto('/#/rehidratar');
    await page.getByRole('combobox', { name: 'Genie Space em produção' }).click();
    await page.getByRole('option', { name: /Recebíveis/ }).click();
    await page.getByRole('button', { name: 'Continuar' }).click();
    await page.getByRole('button', { name: 'Confirmar rehidratação' }).click();

    await expect(page.getByText('Você não tem acesso a este espaço.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });
});

// --- 2. the promotion-detail entry point ----------------------------------------------------

test.describe('"Exportar para dev" on a deployed promotion\'s own detail', () => {
  const summary = {
    id: 'p-deployed', resource_id: 'prod-1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'malcoln@databricks.com', pr_number: 12, pr_url: 'https://gh/pr/12',
    current_phase: 'deployed', terminal: true,
    created_at: '2026-07-01T10:00:00Z', updated_at: '2026-07-01T10:05:00Z',
  };
  const review = {
    findings: [], gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto.' },
    eval: { status: 'pass', summary: 'ok' }, allowlist_violations: [], consumer_group: '',
    timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
  };
  const liveStatus = {
    pr_state: 'closed', merged: true, checks: 'success', review_decision: 'approved',
    deploy: { status: 'success', conclusion: 'success', waiting_approval: false, run_url: null },
    pr_url: 'https://gh/pr/12', phase: 'deployed',
  };

  test.beforeEach(async ({ page }) => {
    let audit: Record<string, unknown>[] = [];
    await page.route('**/api/promotions/p-deployed', (r) =>
      r.fulfill({ json: { promotion: summary, review, pr: { number: 12, url: 'https://gh/pr/12' },
                          live_status: liveStatus, audit } }));
    await page.route('**/api/promotions/p-deployed/audit', (r) => r.fulfill({ json: { audit } }));
    await page.route('**/api/promote/12/status', (r) => r.fulfill({ json: liveStatus }));
    await page.route('**/api/rehydrate', (route) => {
      audit = [{ seq: 1, event_type: 'rehydrated', occurred_at: '2026-07-13T00:00:00Z',
                actor_github_login: null, actor_app_email: 'malcoln@databricks.com',
                github_event_at: null, detail: { mode: 'create' } }];
      return route.fulfill({ json: { space_id: 'dev-new-2', mode: 'create', title: 'Recebíveis (dev)' } });
    });
  });

  test('the action is discoverable on the detail, rehydrates, and the event lands in the audit trail', async ({ page }) => {
    await page.goto('/#/promocoes/p-deployed');

    // Discoverable WITHOUT digging: it's right on the detail, with an explanatory hint.
    await expect(page.getByRole('button', { name: 'Exportar para dev' })).toBeVisible();
    await expect(page.getByText('Traz este Space de volta para o dev')).toBeVisible();

    await page.getByRole('button', { name: 'Exportar para dev' }).click();
    await page.getByRole('button', { name: 'Continuar' }).click();
    await page.getByRole('button', { name: 'Confirmar rehidratação' }).click();

    await expect(page.getByText('Space criado no dev:')).toBeVisible();
    await expect(
      page.getByRole('link', { name: 'Abrir no dev ↗' }),
    ).toHaveAttribute('href', 'https://dev.cloud.databricks.com/genie/rooms/dev-new-2');

    // The audit trail (already on this same detail screen) picks up the new event immediately.
    await expect(page.getByText('Rehidratado para dev')).toBeVisible();
  });
});

import { test, expect } from '@playwright/test';

// Contextual prod-to-dev export remains available from a deployed promotion detail.

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({
      json: {
        email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', is_admin: false,
        dev_host: 'dev.cloud.databricks.com',
      },
    }),
  );
  // G6: the de-para preview every "Continuar" click now loads before 'confirming' — a single,
  // one-table default here; tests that care about the de-para specifics override this route.
  await page.route('**/api/rehydrate/preview**', (route) =>
    route.fulfill({
      json: {
        title: 'Recebíveis',
        tables: [{
          source: 'prod_recebiveis.diamond.fato_recebiveis',
          default_target: 'dev_recebiveis.diamond.fato_recebiveis',
          dev_suggestions: [],
        }],
      },
    }),
  );
});

// --- promotion-detail entry point -----------------------------------------------------------

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

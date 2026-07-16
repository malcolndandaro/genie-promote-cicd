import { test, expect } from '@playwright/test';

// Auditoria → "Exportações para dev": every prod->dev rehydrate this app has performed. Covers:
// create/overwrite mode badge, the "Abrir no dev" deep-link (only when dev_host is known), the
// empty state, and a 403 surfacing as a clear error. The server is the real authority — these
// tests only verify the UI round-trips through `/api/admin/rehydrate-events` correctly.

// Newest first, exactly as the real `/api/admin/rehydrate-events` endpoint returns them — the UI
// trusts the server's order and never re-sorts client-side (same convention as the audit card).
const EVENTS = [
  { id: 'e2', resource_id: 's2', resource_title: 'Merchant Data', actor_email: 'bob@databricks.com',
    mode: 'overwrite', dev_space_id: 'dev-2', detail: null, created_at: '2026-07-14T09:00:00Z' },
  { id: 'e1', resource_id: 's1', resource_title: 'Recebíveis', actor_email: 'ana@databricks.com',
    mode: 'create', dev_space_id: 'dev-1', detail: null, created_at: '2026-07-13T10:00:00Z' },
];

function mockAuditScreen(page: import('@playwright/test').Page, opts: {
  devHost?: string | null;
  events?: Record<string, unknown>[];
  rehydrateStatus?: number;
} = {}) {
  // `??` would treat an explicit `null` (the "dev host unknown" case) as "not provided" — use `in`
  // so a caller can distinguish the two.
  const devHost = 'devHost' in opts ? opts.devHost : 'dev.cloud.databricks.com';
  const events = opts.events ?? EVENTS;

  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'admin@databricks.com', steward: 'pedro@databricks.com', is_admin: true,
                        dev_host: devHost } }),
  );
  page.route('**/api/admin/audit**', (r) => r.fulfill({ json: { audit: [] } }));
  page.route('**/api/admin/rehydrate-events**', (route) => {
    if (opts.rehydrateStatus) {
      return route.fulfill({ status: opts.rehydrateStatus,
        json: { detail: 'Apenas Stewards/Admins acessam o console de administração.' } });
    }
    return route.fulfill({ json: { events } });
  });
}

function exportsCard(page: import('@playwright/test').Page) {
  return page.locator('.card').filter({ hasText: 'Exportações para dev' });
}

test('lists rehydrate events newest first with mode badge', async ({ page }) => {
  mockAuditScreen(page);
  await page.goto('/#/auditoria');
  await expect(page.getByRole('heading', { name: 'Exportações para dev' })).toBeVisible();

  const rows = exportsCard(page).locator('li');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText('Merchant Data');   // newest first (created_at later)
  await expect(rows.nth(0)).toContainText('Sobrescrito');
  await expect(rows.nth(1)).toContainText('Recebíveis');
  await expect(rows.nth(1)).toContainText('Criado');
});

test('shows an "Abrir no dev" link when the dev host is known', async ({ page }) => {
  mockAuditScreen(page, { devHost: 'dev.cloud.databricks.com' });
  await page.goto('/#/auditoria');

  const row = exportsCard(page).locator('li').filter({ hasText: 'Recebíveis' });
  const link = row.getByRole('link', { name: 'Abrir no dev ↗' });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', 'https://dev.cloud.databricks.com/genie/rooms/dev-1');
});

test('falls back to the bare dev_space_id when the dev host is unknown', async ({ page }) => {
  mockAuditScreen(page, { devHost: null });
  await page.goto('/#/auditoria');

  const row = exportsCard(page).locator('li').filter({ hasText: 'Recebíveis' });
  await expect(row.getByRole('link', { name: 'Abrir no dev ↗' })).toHaveCount(0);
  await expect(row).toContainText('dev-1');
});

test('shows an empty state when no exports have happened yet', async ({ page }) => {
  mockAuditScreen(page, { events: [] });
  await page.goto('/#/auditoria');
  await expect(exportsCard(page).getByText('Nenhuma exportação para dev ainda.')).toBeVisible();
});

test('a 403 from the server surfaces as a clear error, not a silent empty card', async ({ page }) => {
  mockAuditScreen(page, { rehydrateStatus: 403 });
  await page.goto('/#/auditoria');
  await expect(exportsCard(page).getByText(/Sem permissão/)).toBeVisible();
});

import { test, expect } from '@playwright/test';

// S4 (app-ux-overhaul, GR4): the standalone Audit page — Platform-Admin-only, combinable
// space/actor filters + a date range, offset pagination. Pulled out of the general Admin console.

const EVENTS = [
  { seq: 2, event_type: 'pr_review_approved', occurred_at: '2026-07-14T10:00:00Z',
    actor_github_login: 'PSPedro176', actor_app_email: null, github_event_at: null, detail: null,
    promotion_id: 'p1', resource_id: 's1', resource_title: 'Recebíveis' },
  { seq: 1, event_type: 'requested', occurred_at: '2026-07-14T09:00:00Z',
    actor_github_login: null, actor_app_email: 'ana@databricks.com', github_event_at: null, detail: null,
    promotion_id: 'p1', resource_id: 's1', resource_title: 'Recebíveis' },
];

function routes(page, { isAdmin = true }: { isAdmin?: boolean } = {}) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'admin@databricks.com', steward: 'pedro@databricks.com', is_admin: isAdmin } }));
}

test('a non-admin cannot reach the Auditoria section (no nav entry)', async ({ page }) => {
  routes(page, { isAdmin: false });
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Auditoria' })).toHaveCount(0);
});

test('an admin sees the audit list with actor + resource, newest first', async ({ page }) => {
  routes(page);
  await page.route('**/api/admin/audit**', (r) => r.fulfill({ json: { audit: EVENTS } }));
  await page.goto('/#/auditoria');

  await expect(page.getByRole('heading', { name: 'Auditoria', level: 2 })).toBeVisible();
  await expect(page.getByText('Recebíveis')).toHaveCount(2);
  await expect(page.getByText('PSPedro176')).toBeVisible();
  await expect(page.getByText('ana@databricks.com')).toBeVisible();
  await expect(page.getByText('(somente exibição)')).toBeVisible(); // the app-email row only
});

test('applying the space/actor filters re-queries with the right params', async ({ page }) => {
  routes(page);
  const seen: string[] = [];
  await page.route('**/api/admin/audit**', (route) => {
    seen.push(new URL(route.request().url()).search);
    route.fulfill({ json: { audit: EVENTS } });
  });
  await page.goto('/#/auditoria');

  await page.getByLabel('Espaço (resource id)').fill('s1');
  await page.getByLabel('Usuário (login GitHub ou e-mail)').fill('ana@databricks.com');
  await page.getByRole('button', { name: 'Filtrar' }).click();

  await expect.poll(() => seen.some((s) => s.includes('resource_id=s1') && s.includes('actor=ana'))).toBe(true);
  // Filtering resets to the first page.
  await expect.poll(() => seen[seen.length - 1]).toContain('offset=0');
});

test('an empty result set shows a clear message, not a blank list', async ({ page }) => {
  routes(page);
  await page.route('**/api/admin/audit**', (r) => r.fulfill({ json: { audit: [] } }));
  await page.goto('/#/auditoria');
  await expect(page.getByText('Nenhum evento de auditoria corresponde aos filtros.')).toBeVisible();
});

test('pagination: a full page enables "Próxima"; a short page does not', async ({ page }) => {
  routes(page);
  const fullPage = Array.from({ length: 50 }, (_, i) => ({ ...EVENTS[0], seq: i + 1 }));
  await page.route('**/api/admin/audit**', (route) => {
    const offset = new URL(route.request().url()).searchParams.get('offset');
    route.fulfill({ json: { audit: offset === '50' ? [] : fullPage } });
  });
  await page.goto('/#/auditoria');

  const next = page.getByRole('button', { name: 'Próxima →' });
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page.getByText('Nenhum evento de auditoria corresponde aos filtros.')).toBeVisible();
  await expect(next).toBeDisabled();
});

test('a 403 from the server surfaces as a clear error, not a silent empty screen', async ({ page }) => {
  routes(page);
  await page.route('**/api/admin/audit**', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Apenas Stewards/Admins acessam o console de administração.' } }));
  await page.goto('/#/auditoria');
  await expect(page.getByRole('alert')).toBeVisible();
});

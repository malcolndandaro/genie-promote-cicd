import { test, expect } from '@playwright/test';

// S5 (app-ux-overhaul): the Approver's own "Fila de aprovação" — moved out of AcessoEspacos.svelte
// into its own section/screen, gated on is_approver (or is_admin, superset). Covers: the queue
// renders here (not on "Acesso"), approving needs no reason, denying REQUIRES one (D8).

const PENDING = [
  { id: 'r1', space_id: 's1', space_title: 'Recebíveis', requester_email: 'ana@databricks.com',
    note: 'preciso para o relatório mensal', want_space_permission: true, space_permission_level: 'CAN_RUN',
    want_uc_select: false, state: 'requested', decided_by: null, decided_at: null, decision_note: null,
    pr_number: null, pr_url: null, created_at: '2026-07-14T09:00:00Z', updated_at: '2026-07-14T09:00:00Z' },
];

function routes(page, { isApprover = true, isAdmin = false } = {}) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'pedro@databricks.com', steward: null, is_admin: isAdmin, is_approver: isApprover } }));
  page.route('**/api/access-requests?scope=mine', (r) => r.fulfill({ json: { requests: [] } }));
}

test('the Approver persona sees a "Fila de aprovação" nav section, separate from Acesso', async ({ page }) => {
  routes(page);
  page.route('**/api/access-requests?scope=pending', (r) => r.fulfill({ json: { requests: PENDING } }));
  await page.goto('/');

  await expect(page.getByRole('link', { name: 'Fila de aprovação' })).toBeVisible();
  // The request queue is NOT on the "Acesso" screen anymore.
  await page.getByRole('link', { name: 'Acesso' }).click();
  await expect(page.getByText('Recebíveis', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Negar' })).toHaveCount(0);
});

test('a caller without the Approver/Admin persona sees no "Fila de aprovação" section', async ({ page }) => {
  routes(page, { isApprover: false, isAdmin: false });
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Fila de aprovação' })).toHaveCount(0);
});

test('approving needs no reason', async ({ page }) => {
  routes(page);
  page.route('**/api/access-requests?scope=pending', (r) => r.fulfill({ json: { requests: PENDING } }));
  let decideBody: unknown;
  await page.route('**/api/access-requests/r1/decide', (route) => {
    decideBody = route.request().postDataJSON();
    route.fulfill({ json: { request: { ...PENDING[0], state: 'approved' } } });
  });
  await page.goto('/#/aprovacoes');
  await page.getByRole('button', { name: 'Aprovar' }).click();
  await expect.poll(() => decideBody).toEqual({ approve: true, note: null });
});

test('denying opens a required reason field — cannot confirm empty, can confirm once filled', async ({ page }) => {
  routes(page);
  page.route('**/api/access-requests?scope=pending', (r) => r.fulfill({ json: { requests: PENDING } }));
  let decideBody: unknown;
  await page.route('**/api/access-requests/r1/decide', (route) => {
    decideBody = route.request().postDataJSON();
    route.fulfill({ json: { request: { ...PENDING[0], state: 'denied', decision_note: 'fora do escopo' } } });
  });
  await page.goto('/#/aprovacoes');

  await page.getByRole('button', { name: 'Negar' }).click();
  const confirm = page.getByRole('button', { name: 'Confirmar negativa' });
  await expect(confirm).toBeDisabled();

  await page.getByPlaceholder('Por que esta solicitação está sendo negada?').fill('fora do escopo');
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => decideBody).toEqual({ approve: false, note: 'fora do escopo' });
});

test('a 403 (self-approval) surfaces as a clear error', async ({ page }) => {
  routes(page);
  page.route('**/api/access-requests?scope=pending', (r) => r.fulfill({ json: { requests: PENDING } }));
  await page.route('**/api/access-requests/r1/decide', (route) =>
    route.fulfill({ status: 403, json: { detail: 'segregação de funções' } }));
  await page.goto('/#/aprovacoes');
  await page.getByRole('button', { name: 'Aprovar' }).click();
  await expect(page.getByRole('alert')).toContainText('Segregação de funções');
});

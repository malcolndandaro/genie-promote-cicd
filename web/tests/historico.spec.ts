import { test, expect } from '@playwright/test';

// LB5: the cross-user history + role-gated scope toggle (admins see all) — now living inside the
// merged "Meus espaços" page (S3/D3) rather than a standalone "Minhas promoções" screen. History
// is nested per-space: expand a space's row to see its attempts and open one.

const review = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto.' },
  eval: { status: 'pass', summary: 'ok' },
  allowlist_violations: [],
  timeline: [{ key: 'review', label: 'Revisão', status: 'pass' }],
};
const mine = [
  { id: 'p-mine', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
    requester_email: 'ana@databricks.com', pr_number: 6, pr_url: 'https://gh/pr/6',
    current_phase: 'open', terminal: false,
    created_at: '2026-06-26T10:00:00Z', updated_at: '2026-06-26T10:05:00Z' },
];
const all = [
  ...mine,
  { id: 'p-bob', resource_id: 'sp2', resource_kind: 'genie_space', resource_title: 'Cedentes',
    requester_email: 'bob@databricks.com', pr_number: 7, pr_url: 'https://gh/pr/7',
    current_phase: 'deployed', terminal: true,
    created_at: '2026-06-25T10:00:00Z', updated_at: '2026-06-25T10:05:00Z' },
];

function routes(page, { isAdmin }: { isAdmin: boolean }) {
  page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: isAdmin } }));
  page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: mine } }));
  page.route('**/api/promotions?scope=all', (r) => r.fulfill({ json: { promotions: all } }));
  page.route('**/api/promotions/p-mine', (r) =>
    r.fulfill({ json: { promotion: mine[0], review, pr: { number: 6, url: 'https://gh/pr/6' }, live_status: null, audit: [] } }));
  page.route('**/api/promote/6/status', (r) =>
    r.fulfill({ json: { pr_state: 'open', merged: false, checks: 'pending', review_decision: 'review_required',
      deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null }, pr_url: 'https://gh/pr/6', phase: 'checks_running' } }));
}

test('a non-admin sees only their promotions; expanding a row and opening it shows the stored review (no re-run)', async ({ page }) => {
  let promoteCalled = false;
  await page.route('**/api/promote', (r) => { promoteCalled = true; r.fulfill({ status: 500, json: {} }); });
  routes(page, { isAdmin: false });
  await page.goto('/');
  await page.getByRole('link', { name: 'Meus espaços' }).click();

  await expect(page.getByText('Recebíveis')).toBeVisible();
  await expect(page.getByText('Cedentes')).toHaveCount(0); // scope=mine only
  await expect(page.getByText('Todas (Steward/Admin)')).toHaveCount(0); // no scope toggle for a non-admin

  await page.getByRole('button', { name: /Expandir histórico de/ }).click();
  await page.getByRole('button', { name: 'Abrir promoção: Recebíveis' }).click();
  // The exact STORED snapshot fills the contextual right panel — no route change and no re-run.
  await expect(page).toHaveURL(/#\/espacos$/);
  const panel = page.locator('.working-panel');
  await expect(panel.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(panel.getByText('🟢 Pronto.')).toBeVisible();
  expect(promoteCalled).toBe(false);
});

test('clicking a Space card opens its most recent run in the right panel', async ({ page }) => {
  routes(page, { isAdmin: false });
  await page.goto('/#/espacos');

  await page.getByRole('button', { name: 'Abrir promoção mais recente: Recebíveis' }).click();

  await expect(page).toHaveURL(/#\/espacos$/);
  const panel = page.locator('.working-panel');
  await expect(panel.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(panel.getByText('🟢 Pronto.')).toBeVisible();
});

test('an admin gets the scope toggle and can list all promotions, grouped by space', async ({ page }) => {
  routes(page, { isAdmin: true });
  await page.goto('/');
  await page.getByRole('link', { name: 'Meus espaços' }).click();

  await expect(page.getByRole('button', { name: 'Todas (Steward/Admin)' })).toBeVisible();
  await page.getByRole('button', { name: 'Todas (Steward/Admin)' }).click();
  // The cross-user view groups another author's promotion under its OWN space row too.
  await expect(page.getByText('Cedentes')).toBeVisible();
  await expect(page.getByText('Recebíveis')).toBeVisible();
});

test('the open promotion on a space is visible on its row before expanding (D7)', async ({ page }) => {
  routes(page, { isAdmin: false });
  await page.goto('/');
  await page.getByRole('link', { name: 'Meus espaços' }).click();

  // The resumed run's live phase (not its older list snapshot) is visible on the collapsed row.
  await expect(page.getByLabel('Spaces disponíveis').getByText('Checagens em execução')).toBeVisible();
  await expect(page.getByText('— ana@databricks.com')).toBeVisible();
});

test('a stale merged summary is reconciled to the live deployed status before the Space is clicked', async ({ page }) => {
  const stale = {
    ...mine[0],
    id: 'p-stale',
    pr_number: 13,
    pr_url: 'https://gh/pr/13',
    current_phase: 'merged',
    terminal: false,
  };
  let releaseStatus!: () => void;
  let markStatusStarted!: () => void;
  const statusGate = new Promise<void>((resolve) => { releaseStatus = resolve; });
  const statusStarted = new Promise<void>((resolve) => { markStatusStarted = resolve; });
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }));
  await page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  await page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: [stale] } }));
  await page.route('**/api/promotions/p-stale', (r) => r.fulfill({ json: {
    promotion: stale,
    review,
    pr: { number: 13, url: 'https://gh/pr/13' },
    live_status: {
      pr_state: 'closed', merged: true, checks: 'success', review_decision: 'approved',
      deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
      pr_url: 'https://gh/pr/13', phase: 'merged',
    },
    audit: [],
  } }));
  await page.route('**/api/promotions/p-stale/audit', (r) => r.fulfill({ json: { audit: [] } }));
  await page.route('**/api/promote/13/status', (r) => {
    markStatusStarted();
    return statusGate.then(() => r.fulfill({ json: {
      pr_state: 'closed', merged: true, checks: 'success', review_decision: 'approved',
      deploy: { status: 'completed', conclusion: 'success', waiting_approval: false,
        run_url: 'https://gh/actions/13', run_id: 13 },
      pr_url: 'https://gh/pr/13', phase: 'deployed', prod_space_id: 'prod-13',
    } }));
  });

  await page.goto('/#/espacos');
  await statusStarted;

  const spaces = page.getByLabel('Spaces disponíveis');
  await expect(spaces.getByText('Atualizando status…')).toBeVisible();
  await expect(spaces.getByText('Merge concluído')).toHaveCount(0);
  await expect(page.getByLabel('Atualizando status no GitHub')).toBeVisible();
  await expect(page.getByText('A publicação aguarda a decisão do Steward')).toHaveCount(0);

  releaseStatus();
  await expect(spaces.getByText('Implantado em produção')).toBeVisible();
});

test('expanding support details shows progress while GitHub evidence is loading', async ({ page }) => {
  const stale = {
    ...mine[0], id: 'p-loading', pr_number: 14, pr_url: 'https://gh/pr/14',
    current_phase: 'merged', terminal: false,
  };
  let releaseEvidence!: () => void;
  let markEvidenceStarted!: () => void;
  const evidenceGate = new Promise<void>((resolve) => { releaseEvidence = resolve; });
  const evidenceStarted = new Promise<void>((resolve) => { markEvidenceStarted = resolve; });
  let coreStatusReads = 0;
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: 'ana@databricks.com', steward: 'pedro@databricks.com', is_admin: false } }));
  await page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  await page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: [stale] } }));
  await page.route('**/api/promotions/p-loading', (r) => r.fulfill({ json: {
    promotion: stale, review, pr: { number: 14, url: 'https://gh/pr/14' },
    live_status: {
      pr_state: 'closed', merged: true, checks: 'success', review_decision: 'approved',
      deploy: { status: 'in_progress', conclusion: null, waiting_approval: false,
        run_url: 'https://gh/actions/14', run_id: 14 },
      pr_url: 'https://gh/pr/14', phase: 'merged',
    },
    audit: [],
  } }));
  await page.route('**/api/promotions/p-loading/audit', (r) => r.fulfill({ json: { audit: [] } }));
  await page.route('**/api/promote/14/status**', (r) => {
    if (!new URL(r.request().url()).searchParams.has('include_deployment_evidence')) {
      coreStatusReads += 1;
      return r.fulfill({ json: {
        pr_state: 'closed', merged: true, checks: 'success', review_decision: 'approved',
        deploy: { status: 'in_progress', conclusion: null, waiting_approval: false,
          run_url: 'https://gh/actions/14', run_id: 14 },
        pr_url: 'https://gh/pr/14', phase: 'deploying',
      } });
    }
    markEvidenceStarted();
    return evidenceGate.then(() => r.fulfill({ json: {
      pr_state: 'closed', merged: true, checks: 'success', review_decision: 'approved',
      deploy: { status: 'in_progress', conclusion: null, waiting_approval: false,
        run_url: 'https://gh/actions/14', run_id: 14,
        steps: [{ name: 'Checkout content', status: 'completed', conclusion: 'success', number: 1,
          job_name: 'deploy', details_url: 'https://gh/actions/14' }] },
      pr_url: 'https://gh/pr/14', phase: 'deploying',
    } }));
  });

  await page.goto('/#/espacos');
  await expect(page.getByLabel('Spaces disponíveis').getByText('Implantando…')).toBeVisible();
  await page.getByText('Detalhes para suporte e auditoria', { exact: true }).click();
  await evidenceStarted;

  await expect(page.getByLabel('Carregando detalhes do GitHub Actions')).toBeVisible();
  releaseEvidence();
  await expect(page.getByText('Checkout content', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Carregando detalhes do GitHub Actions')).toHaveCount(0);
  expect(coreStatusReads).toBe(1);
});

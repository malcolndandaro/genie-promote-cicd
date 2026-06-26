import { test, expect, type Page } from '@playwright/test';

// SoD approval view (SV4/GH4), now IDENTITY-DERIVED (no manual persona toggle):
//  - the requester only ever waits for the Steward (even if they're also a Steward — it's "mine");
//  - the Steward, opening SOMEONE ELSE'S promotion from "Todas", gets the "Aprovar no GitHub" link.
// The real SoD is always the GitHub Environment gate (prevent_self_review).

const PR = { number: 42, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42' };
const RUN_URL = 'https://github.com/malcolndandaro/genie-promote-cicd/actions/runs/9';

const cleanReview = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [{ key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' }],
};
const blockerReview = {
  ...cleanReview,
  findings: [{ rule_id: 'EVAL-01', severity: 'BLOCKER', message: 'Poucas perguntas de benchmark.' }],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada.' },
};

const deployStatus = (phase: string, extra: Record<string, unknown> = {}) => ({
  pr_state: 'closed',
  merged: true,
  checks: 'success',
  review_decision: 'approved',
  deploy: {
    status: phase === 'awaiting_approval' ? 'waiting' : 'completed',
    conclusion: phase === 'deployed' ? 'success' : phase === 'deploy_failed' ? 'failure' : null,
    waiting_approval: phase === 'awaiting_approval',
    run_url: RUN_URL,
    run_id: 9,
    approver: null,
    ...extra,
  },
  pr_url: PR.url,
  phase,
});

const summary = (requester: string) => ({
  id: 'p1', resource_id: 'sp1', resource_kind: 'genie_space', resource_title: 'Recebíveis',
  requester_email: requester, pr_number: PR.number, pr_url: PR.url,
  current_phase: 'awaiting_approval', terminal: false,
  created_at: '2026-06-26T10:00:00Z', updated_at: '2026-06-26T10:05:00Z',
});

// The requester (author) flow: they request their own promotion and see the "mine" view.
async function requestAsAuthor(page: Page, review: unknown, viewer = 'malcoln@databricks.com') {
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: viewer, steward: 'pedro@databricks.com', is_admin: false } }));
  await page.route('**/api/spaces', (r) =>
    r.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }));
  await page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/promote', (r) => r.fulfill({ json: { review, pr: PR, promotion_id: 'p1' } }));
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();
  await expect(page.getByRole('heading', { name: 'Aprovação do Steward' })).toBeVisible();
}

// The Steward (or any viewer) opening SOMEONE ELSE'S promotion from the "Todas" history.
async function openFromHistory(
  page: Page,
  { viewer, isAdmin, requester, review, status }:
    { viewer: string; isAdmin: boolean; requester: string; review: unknown; status: unknown },
) {
  const s = summary(requester);
  await page.route('**/api/whoami', (r) =>
    r.fulfill({ json: { email: viewer, steward: 'pedro@databricks.com', is_admin: isAdmin } }));
  await page.route('**/api/spaces', (r) => r.fulfill({ json: { spaces: [] } }));
  await page.route('**/api/promotions?scope=mine', (r) => r.fulfill({ json: { promotions: [] } }));
  await page.route('**/api/promotions?scope=all', (r) => r.fulfill({ json: { promotions: [s] } }));
  await page.route('**/api/promotions/p1', (r) =>
    r.fulfill({ json: { promotion: s, review, pr: PR, live_status: status, audit: [] } }));
  await page.route(`**/api/promote/${PR.number}/status`, (r) => r.fulfill({ json: status }));
  await page.goto('/');
  await page.getByRole('tab', { name: 'Minhas promoções' }).click();
  await page.getByRole('button', { name: 'Todas (Steward/Admin)' }).click();
  await page.getByRole('button', { name: /Abrir/ }).click();
  await expect(page.getByRole('heading', { name: 'Aprovação do Steward' })).toBeVisible();
}

test('the requester only ever waits for the Steward (cannot approve their own)', async ({ page }) => {
  await requestAsAuthor(page, cleanReview);
  await expect(page.getByText(/não pode aprovar a própria promoção/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('a Steward who is also the requester of their own space cannot self-approve (SoD)', async ({ page }) => {
  // The steward (pedro) requests their OWN promotion -> "mine" -> only the waiting view, no link.
  await requestAsAuthor(page, cleanReview, 'pedro@databricks.com');
  await expect(page.getByText(/não pode aprovar a própria promoção/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('a non-Steward admin opening another user\'s promotion sees no approve affordance', async ({ page }) => {
  await openFromHistory(page, {
    viewer: 'malcoln@databricks.com', isAdmin: true, requester: 'ana@databricks.com',
    review: cleanReview, status: deployStatus('awaiting_approval'),
  });
  await expect(page.getByText('Apenas o Steward aprova esta promoção.')).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('the Steward gets the "Aprovar no GitHub" deep-link for another user\'s waiting gate', async ({ page }) => {
  await openFromHistory(page, {
    viewer: 'pedro@databricks.com', isAdmin: true, requester: 'malcoln@databricks.com',
    review: cleanReview, status: deployStatus('awaiting_approval'),
  });
  const link = page.getByRole('link', { name: /Aprovar no GitHub/ });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', RUN_URL); // the bot never approves — the human does, on GitHub
});

test('the Steward is blocked from approving while a BLOCKER finding stands', async ({ page }) => {
  await openFromHistory(page, {
    viewer: 'pedro@databricks.com', isAdmin: true, requester: 'malcoln@databricks.com',
    review: blockerReview, status: deployStatus('awaiting_approval'),
  });
  await expect(page.getByText(/bloqueada por achados BLOCKER/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('the Steward view reflects a completed deploy with the approver login', async ({ page }) => {
  await openFromHistory(page, {
    viewer: 'pedro@databricks.com', isAdmin: true, requester: 'malcoln@databricks.com',
    review: cleanReview, status: deployStatus('deployed', { approver: 'pedro-gh' }),
  });
  await expect(page.getByText(/Implantado em produção — aprovado por pedro-gh/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

test('the Steward view reflects a failed deploy (no approve link)', async ({ page }) => {
  await openFromHistory(page, {
    viewer: 'pedro@databricks.com', isAdmin: true, requester: 'malcoln@databricks.com',
    review: cleanReview, status: deployStatus('deploy_failed'),
  });
  await expect(page.getByText(/Falha no deploy de produção/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Aprovar no GitHub/ })).toHaveCount(0);
});

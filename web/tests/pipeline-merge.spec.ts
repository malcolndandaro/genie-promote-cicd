import { test, expect } from '@playwright/test';

// GH5: the merge + PR-review (merge-approval) gate are first-class, LIVE-monitored pipeline steps.
// The first 3 steps come from the review verdict; pr_review/merge/approval/deploy are driven by the
// polled live status — reflect, never assert.

const PR = { number: 42, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42' };

const review = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' },
    { key: 'eval', label: 'Eval-run (advisory)', status: 'pass' },
  ],
};

const liveStatus = (over: Record<string, unknown> = {}) => ({
  pr_state: 'open',
  merged: false,
  checks: 'success',
  review_decision: 'approved',
  deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null, run_id: null, approver: null },
  pr_url: PR.url,
  phase: 'open',
  ...over,
});

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } }));
});

async function promote(page: import('@playwright/test').Page, status: unknown) {
  await page.route(`**/api/promote/${PR.number}/status`, (route) => route.fulfill({ json: status }));
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await page.getByRole('button', { name: 'Confirmar promoção' }).click(); // G3: confirm the chosen space
}

// The Pipeline renders a visually-hidden status word per step inside its listitem
// ("— concluído"=pass, "— reprovado"=fail, "— em execução"=running, "— pendente"=pending),
// so we assert STATUS, not just visibility.
const step = (page: import('@playwright/test').Page, label: string) =>
  page.getByRole('listitem').filter({ hasText: label });

test('PR-review + Merge are distinct steps; approved/open → PR-review concluído, Merge em execução', async ({
  page,
}) => {
  await promote(page, liveStatus());
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  // The verdict steps are still present (unchanged from the review).
  await expect(page.getByText('Revisão do agente (Genie Reviewer)')).toBeVisible();
  // The PR is approved but not yet merged → PR-review passed, Merge is running (preconditions met).
  await expect(step(page, 'Revisão do PR (aprovação de merge)')).toContainText('concluído');
  await expect(step(page, 'Merge para main')).toContainText('em execução');
});

test('changes_requested → PR-review step shows reprovado', async ({ page }) => {
  await promote(page, liveStatus({ review_decision: 'changes_requested' }));
  await expect(step(page, 'Revisão do PR (aprovação de merge)')).toContainText('reprovado');
});

test('a merged PR shows the "Merge para main" step as concluído', async ({ page }) => {
  await promote(page, liveStatus({ merged: true, pr_state: 'closed', phase: 'merged' }));
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(step(page, 'Merge para main')).toContainText('concluído');
});

test('a failed deploy → Deploy step shows reprovado', async ({ page }) => {
  await promote(
    page,
    liveStatus({
      merged: true,
      pr_state: 'closed',
      deploy: { status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: 'r', run_id: 9, approver: null },
      phase: 'deploy_failed',
    }),
  );
  await expect(step(page, 'Deploy em produção (service principal)')).toContainText('reprovado');
});

// G8: "por que falhou?" without leaving the app — a live GitHub check-run failure escalates the
// app's own "Checagens" verdict step + surfaces the CI's own (PT) findings in an expandable panel.
test('checks_failed → "Checagens" step shows reprovado with an expandable PT detail panel + GitHub link', async ({
  page,
}) => {
  const detail = [
    {
      name: 'GRANT-01 — every declared principal can SELECT the prod tables',
      conclusion: 'failure',
      summary: "🔴 GRANT-01 — promoção bloqueada (1 achado(s))\n'users' não tem SELECT em prod_recebiveis.diamond.dim_arranjo",
      details_url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42/checks',
    },
  ];
  await promote(page, liveStatus({ checks: 'failure', phase: 'checks_failed', checks_detail: detail }));
  await expect(step(page, 'Checagens determinísticas')).toContainText('reprovado');

  // Closed by default — must expand the disclosure to reveal the CI's own PT findings.
  await page.getByText('Ver detalhes das checagens').click();
  await expect(page.getByText("'users' não tem SELECT em prod_recebiveis.diamond.dim_arranjo")).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ver log no GitHub ↗' })).toHaveAttribute(
    'href',
    detail[0].details_url,
  );
});

test('checks_failed with no fetched detail (bot read hiccup) → still reprovado, no panel, no crash', async ({
  page,
}) => {
  await promote(page, liveStatus({ checks: 'failure', phase: 'checks_failed', checks_detail: null }));
  await expect(step(page, 'Checagens determinísticas')).toContainText('reprovado');
  await expect(page.getByText('Ver detalhes das checagens')).toHaveCount(0);
});

// Fix C: mirrors G8 one level down — a merged PR whose prod DEPLOY run failed (e.g. apply_access.py
// crashing on real declared access) must surface WHY in the app, not a bare "Falha".
test('deploy_failed → Deploy step shows reprovado with an expandable PT detail panel + GitHub link', async ({
  page,
}) => {
  await promote(
    page,
    liveStatus({
      merged: true,
      pr_state: 'closed',
      deploy: { status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: 'r', run_id: 9, approver: null },
      phase: 'deploy_failed',
      deploy_detail: {
        failed_step: 'Apply declared access',
        summary: "AttributeError: 'dict' object has no attribute 'as_dict'",
        details_url: 'https://github.com/malcolndandaro/genie-promote-cicd/actions/runs/9/jobs/101',
      },
    }),
  );
  await expect(step(page, 'Deploy em produção (service principal)')).toContainText('reprovado');

  // Closed by default — must expand the disclosure to reveal WHY the deploy step failed.
  await page.getByText('Ver detalhes do deploy').click();
  await expect(page.getByText("AttributeError: 'dict' object has no attribute 'as_dict'")).toBeVisible();
  await expect(page.getByRole('link', { name: 'Ver log no GitHub ↗' })).toHaveAttribute(
    'href',
    'https://github.com/malcolndandaro/genie-promote-cicd/actions/runs/9/jobs/101',
  );
});

test('deploy_failed with no fetched detail (bot read hiccup) → still reprovado, no panel, no crash', async ({
  page,
}) => {
  await promote(
    page,
    liveStatus({
      merged: true,
      pr_state: 'closed',
      deploy: { status: 'completed', conclusion: 'failure', waiting_approval: false, run_url: 'r', run_id: 9, approver: null },
      phase: 'deploy_failed',
      deploy_detail: null,
    }),
  );
  await expect(step(page, 'Deploy em produção (service principal)')).toContainText('reprovado');
  await expect(page.getByText('Ver detalhes do deploy')).toHaveCount(0);
});

// W3: "Abrir Genie em produção" — a deep-link to the promoted Space, once it's actually deployed.
// The engine resolves `prod_space_id` server-side (title match against the live prod listing) and
// the SPA only ever renders the link when BOTH that id AND `/api/whoami.prod_host` are present —
// never a guess from a partial answer.
const DEPLOYED = () => liveStatus({
  merged: true,
  pr_state: 'closed',
  deploy: { status: 'completed', conclusion: 'success', waiting_approval: false, run_url: 'r', run_id: 9, approver: 'pedro' },
  phase: 'deployed',
});

test('deployed with a resolved prod_space_id + prod_host → shows the "Abrir Genie em produção" deep-link', async ({
  page,
}) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({
      json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com',
             prod_host: 'prod.example.com' },
    }),
  );
  await promote(page, { ...DEPLOYED(), prod_space_id: 'prod-999' });
  const link = page.getByRole('link', { name: 'Abrir Genie em produção ↗' });
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('href', 'https://prod.example.com/genie/rooms/prod-999');
});

test('deployed but prod_space_id could not be resolved (no/ambiguous title match) → the deep-link is omitted', async ({
  page,
}) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({
      json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com',
             prod_host: 'prod.example.com' },
    }),
  );
  await promote(page, DEPLOYED()); // no prod_space_id on the status payload
  await expect(page.getByText('Implantado em produção')).toBeVisible(); // deployed, still rendered
  await expect(page.getByRole('link', { name: 'Abrir Genie em produção ↗' })).toHaveCount(0);
});

test('deployed with prod_space_id but no prod_host configured → the deep-link is omitted', async ({ page }) => {
  // beforeEach's default /api/whoami mock has no prod_host — no override needed here.
  await promote(page, { ...DEPLOYED(), prod_space_id: 'prod-999' });
  await expect(page.getByText('Implantado em produção')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Abrir Genie em produção ↗' })).toHaveCount(0);
});

test('not yet deployed (merged, awaiting deploy) → the deep-link never shows even with a resolved id', async ({
  page,
}) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({
      json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com',
             prod_host: 'prod.example.com' },
    }),
  );
  // Defensive: the backend only ever resolves prod_space_id when phase === 'deployed' (see
  // engine_api._with_prod_space_id), but the UI itself must ALSO gate on phase, not merely on the
  // field's presence, in case a stale/malformed payload carries it early.
  await promote(page, { ...liveStatus({ merged: true, pr_state: 'closed', phase: 'merged' }), prod_space_id: 'prod-999' });
  await expect(page.getByRole('link', { name: 'Abrir Genie em produção ↗' })).toHaveCount(0);
});

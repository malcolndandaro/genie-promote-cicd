import { test, expect } from '@playwright/test';

// A failing review (mirrors the dev space's EVAL-01 BLOCKER) with a full timeline.
const review = {
  findings: [
    {
      rule_id: 'EVAL-01',
      severity: 'BLOCKER',
      message: 'Apenas 2 perguntas de benchmark — mínimo de 3 para um sinal de eval confiável.',
      citation: 'Genie Promotion Handbook › Eval › EVAL-01',
      suggestion: 'Adicione uma 3ª pergunta de benchmark ao espaço.',
    },
  ],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada — 1 achado BLOCKER.' },
  eval: { status: 'advisory', summary: '🟡 eval-run indisponível neste ambiente (sem CLI).' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'fail' },
    { key: 'eval', label: 'Eval-run (advisory)', status: 'pass' },
    { key: 'approval', label: 'Aprovação do Steward', status: 'running' },
    { key: 'deploy', label: 'Deploy em produção (service principal)', status: 'pending' },
  ],
};

const PR = { number: 42, url: 'https://github.com/malcolndandaro/genie-promote-cicd/pull/42' };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
});

test('promoting renders the review (finding + AI-trust + pipeline + gate) and the PR link', async ({
  page,
}) => {
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  // The opened PR is surfaced with a link to GitHub.
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByRole('link', { name: /Ver no GitHub/ })).toHaveAttribute('href', PR.url);

  // Finding card: rule_id + severity badge + message + suggestion + citation.
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
  await expect(page.getByText('BLOCKER', { exact: true })).toBeVisible();
  await expect(page.getByText(/Apenas 2 perguntas de benchmark/)).toBeVisible();
  await expect(page.getByText(/Adicione uma 3ª pergunta/)).toBeVisible();
  await expect(page.getByText(/Genie Promotion Handbook/)).toBeVisible();

  // AI-trust + a representative pipeline step + the gate.
  await expect(page.getByText('Achados gerados por IA — verifique antes de aprovar.')).toBeVisible();
  await expect(page.getByText(/Leitura dos espaços executa como você \(OBO\)/)).toBeVisible();
  await expect(page.getByText('Revisão do agente (Genie Reviewer)')).toBeVisible();
  await expect(page.getByText(/Promoção bloqueada/)).toBeVisible();
});

test('reflects the live PR status (polled) as a badge in the PR banner', async ({ page }) => {
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } }));
  await page.route(`**/api/promote/${PR.number}/status`, (route) =>
    route.fulfill({
      json: {
        pr_state: 'open',
        merged: false,
        checks: 'pending',
        deploy: { status: 'none', conclusion: null, waiting_approval: false, run_url: null },
        pr_url: PR.url,
        phase: 'checks_running',
      },
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByText('Checagens em execução')).toBeVisible(); // live phase badge (polled)
});

test('shows the animated running pipeline while the promotion is in flight', async ({ page }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  await page.route('**/api/promote', async (route) => {
    await gate;
    await route.fulfill({ json: { review, pr: PR } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.getByText('Executando pipeline de promoção…')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Solicitando…' })).toBeVisible();

  release();
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
});

test('renders a clean (success) review with no findings', async ({ page }) => {
  await page.route('**/api/promote', (route) =>
    route.fulfill({
      json: {
        pr: PR,
        review: {
          findings: [],
          gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
          eval: { status: 'advisory', summary: '🟡 eval indisponível' },
          allowlist_violations: [],
          consumer_group: 'account users',
          timeline: [
            { key: 'checks', label: 'Checagens', status: 'pass' },
            { key: 'review', label: 'Revisão', status: 'pass' },
            { key: 'eval', label: 'Eval-run', status: 'pass' },
            { key: 'approval', label: 'Aprovação do Steward', status: 'running' },
            { key: 'deploy', label: 'Deploy', status: 'pending' },
          ],
        },
      },
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.getByText('Nenhum achado — espaço limpo.')).toBeVisible();
  await expect(page.getByText('🟢 Pronto para promoção.')).toBeVisible();
});

test('a finding without suggestion/citation omits those lines', async ({ page }) => {
  await page.route('**/api/promote', (route) =>
    route.fulfill({
      json: {
        pr: PR,
        review: {
          findings: [{ rule_id: 'ENV-01', severity: 'BLOCKER', message: 'Referência fora do ambiente.' }],
          gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Bloqueado.' },
          eval: { status: 'advisory', summary: '🟡 eval indisponível' },
          allowlist_violations: [],
          consumer_group: 'account users',
          timeline: [{ key: 'checks', label: 'Checagens', status: 'fail' }],
        },
      },
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.getByText('Referência fora do ambiente.')).toBeVisible();
  await expect(page.getByText('↳', { exact: false })).toHaveCount(0); // no suggestion line
});

test('shows an error state with retry when /api/promote fails', async ({ page }) => {
  await page.route('**/api/promote', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: request_promotion' } }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();

  await expect(page.getByText(/Não foi possível solicitar a promoção/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
});

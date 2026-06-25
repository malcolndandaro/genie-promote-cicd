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

test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
});

test('renders the review: finding card, AI disclaimer, exec-identity note, pipeline + gate', async ({
  page,
}) => {
  await page.route('**/api/review', (route) => route.fulfill({ json: review }));
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();

  // Finding card: rule_id + severity badge + message + suggestion + citation.
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
  await expect(page.getByText('BLOCKER', { exact: true })).toBeVisible();
  await expect(page.getByText(/Apenas 2 perguntas de benchmark/)).toBeVisible();
  await expect(page.getByText(/Adicione uma 3ª pergunta/)).toBeVisible();
  await expect(page.getByText(/Genie Promotion Handbook/)).toBeVisible();

  // AI-trust: persistent disclaimer + truthful execution-identity note + identity badge.
  await expect(page.getByText('Achados gerados por IA — verifique antes de aprovar.')).toBeVisible();
  await expect(page.getByText(/Leitura dos espaços executa como você \(OBO\)/)).toBeVisible();
  await expect(page.getByText('malcoln@databricks.com').first()).toBeVisible();

  // Pipeline timeline (a representative step) + gate summary.
  await expect(page.getByText('Revisão do agente (Genie Reviewer)')).toBeVisible();
  await expect(page.getByText(/Promoção bloqueada/)).toBeVisible();
});

test('shows the animated running pipeline while the review is in flight', async ({ page }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  await page.route('**/api/review', async (route) => {
    await gate;
    await route.fulfill({ json: review });
  });
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();

  // In-flight: the running pipeline + the loading button label.
  await expect(page.getByText('Executando pipeline de promoção…')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Revisando…' })).toBeVisible();

  release();
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
});

test('renders a clean (success) review with no findings', async ({ page }) => {
  await page.route('**/api/review', (route) =>
    route.fulfill({
      json: {
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
    }),
  );
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();

  await expect(page.getByText('Nenhum achado — espaço limpo.')).toBeVisible();
  await expect(page.getByText('🟢 Pronto para promoção.')).toBeVisible();
});

test('a finding without suggestion/citation omits those lines', async ({ page }) => {
  await page.route('**/api/review', (route) =>
    route.fulfill({
      json: {
        findings: [{ rule_id: 'ENV-01', severity: 'BLOCKER', message: 'Referência fora do ambiente.' }],
        gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Bloqueado.' },
        eval: { status: 'advisory', summary: '🟡 eval indisponível' },
        allowlist_violations: [],
        consumer_group: 'account users',
        timeline: [{ key: 'checks', label: 'Checagens', status: 'fail' }],
      },
    }),
  );
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();

  await expect(page.getByText('Referência fora do ambiente.')).toBeVisible();
  await expect(page.getByText('↳', { exact: false })).toHaveCount(0); // no suggestion line
});

test('shows an error state with retry when /api/review fails', async ({ page }) => {
  await page.route('**/api/review', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: review_space' } }),
  );
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();

  await expect(page.getByText(/Não foi possível revisar/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
});

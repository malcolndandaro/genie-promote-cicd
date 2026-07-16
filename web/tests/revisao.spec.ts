import { test, expect } from '@playwright/test';
import { confirmPilotPromotion } from './promotion-helpers';

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

test('promoting renders the review (finding + pipeline + gate) and the PR link', async ({
  page,
}) => {
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } }));
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  // The opened PR is surfaced with a link to GitHub.
  await expect(page.getByText('PR de promoção aberto:')).toBeVisible();
  await expect(page.getByRole('link', { name: /Ver no GitHub/ })).toHaveAttribute('href', PR.url);

  // Finding card: rule_id + severity badge + message + suggestion + citation.
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
  await expect(page.getByText('BLOCKER', { exact: true })).toBeVisible();
  await expect(page.getByText(/Apenas 2 perguntas de benchmark/)).toBeVisible();
  await expect(page.getByText(/Adicione uma 3ª pergunta/)).toBeVisible();
  await expect(page.getByText(/Genie Promotion Handbook/)).toBeVisible();

  // A representative pipeline step + the gate.
  await expect(page.getByText('Revisão do agente (Genie Reviewer)')).toBeVisible();
  await expect(page.getByText(/Promoção bloqueada/)).toBeVisible();
});

test('a KA advisory finding renders its markdown (bold/code/list), not raw ** and backticks', async ({ page }) => {
  const kaReview = {
    findings: [{
      rule_id: 'KA:Handbook CI/CD (geral)',
      severity: 'SUGGESTION',
      citation: 'Knowledge Assistant › Handbook CI/CD (geral)',
      message: 'Sua promoção está **bem alinhada**. Catálogo `prod_recebiveis` correto.\n\n**Pontos:**\n1. **Benchmark**: mínimo 2\n2. **Grants**: SELECT em todas',
    }],
    gate: { conclusion: 'success', blocker_count: 0, summary: 'ok' },
    eval: { status: 'advisory', summary: 'x' },
    allowlist_violations: [], consumer_group: 'account users', timeline: [],
  };
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review: kaReview, pr: PR } }));
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  const finding = page.locator('.finding').filter({ hasText: 'KA:Handbook CI/CD' });
  // Bold renders as <strong>, inline code as <code>, list items as <li> — NOT literal markdown.
  await expect(finding.locator('strong', { hasText: 'bem alinhada' })).toBeVisible();
  await expect(finding.locator('code', { hasText: 'prod_recebiveis' })).toBeVisible();
  await expect(finding.locator('li', { hasText: 'Benchmark' })).toBeVisible();
  await expect(finding.locator('li')).toHaveCount(2);
  // The raw markdown markers must NOT appear as visible text.
  await expect(finding.getByText('**bem alinhada**')).toHaveCount(0);
  await expect(finding.getByText('`prod_recebiveis`')).toHaveCount(0);
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
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

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
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

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
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

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
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  await expect(page.getByText('Referência fora do ambiente.')).toBeVisible();
  await expect(page.getByText('↳', { exact: false })).toHaveCount(0); // no suggestion line
});

test('shows an error state with retry when /api/promote fails', async ({ page }) => {
  await page.route('**/api/promote', (route) =>
    route.fulfill({ status: 502, json: { detail: 'engine error: request_promotion' } }),
  );
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  await expect(page.getByText(/Não foi possível solicitar a promoção/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
});

// --- W3: rich eval-run results panel --------------------------------------------------------

test('a block eval state renders the panel with a failures-first question list + dev link', async ({
  page,
}) => {
  const evalReview = {
    ...review,
    eval: {
      status: 'block', pass_rate: 0.5, n: 4, n_correct: 2, n_needs_review: 1, threshold: 0.8,
      run_id: 'run1',
      summary:
        '🔴 O Genie re-executou as 4 perguntas de benchmark e acertou 2 (50%) — abaixo do limiar de 80%.',
      questions: [
        { question: 'Qual o volume cancelado?', status: 'incorrect' },
        { question: 'Quais cedentes têm mais recebíveis?', status: 'needs_review' },
        { question: 'Qual a participação por status?', status: 'correct' },
        { question: 'Quais cedentes estão no RJ?', status: 'correct' },
      ],
    },
  };
  await page.route('**/api/whoami', (route) =>
    route.fulfill({
      json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com', dev_host: 'dev.cloud.databricks.com' },
    }),
  );
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review: evalReview, pr: PR } }));
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  await page.getByText('Ver resultados do eval (4)').click();
  await expect(page.getByText(/O Genie re-executou as 4 perguntas/)).toBeVisible();

  // Failures first: the incorrect question renders above the correct ones.
  const items = page.locator('.eval-panel__item');
  await expect(items).toHaveCount(4);
  await expect(items.nth(0)).toContainText('Qual o volume cancelado?');
  await expect(items.nth(0)).toHaveAttribute('data-status', 'incorrect');
  await expect(items.nth(1)).toContainText('Quais cedentes têm mais recebíveis?');
  await expect(items.nth(1)).toHaveAttribute('data-status', 'needs_review');

  await expect(page.getByRole('link', { name: /Abrir benchmarks no dev/ })).toHaveAttribute(
    'href',
    'https://dev.cloud.databricks.com/genie/rooms/sp1',
  );
});

test('an advisory eval state renders only the explainer + summary, no question list', async ({
  page,
}) => {
  await page.route('**/api/promote', (route) => route.fulfill({ json: { review, pr: PR } })); // eval: advisory, no `questions`
  await page.goto('/#/espacos');
  await page.getByRole('button', { name: 'Solicitar promoção: Recebíveis' }).click();
  await confirmPilotPromotion(page);

  await page.getByText(/Ver resultados do eval/).click();
  await expect(
    page.getByText('Teste automático: o espaço responde corretamente às próprias perguntas de benchmark?'),
  ).toBeVisible();
  await expect(page.getByText(review.eval.summary)).toBeVisible();
  await expect(page.locator('.eval-panel__item')).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Abrir benchmarks no dev/ })).toHaveCount(0); // no dev_host mocked
});

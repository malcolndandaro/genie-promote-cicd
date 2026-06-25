import { test, expect } from '@playwright/test';

const cleanReview = {
  findings: [],
  gate: { conclusion: 'success', blocker_count: 0, summary: '🟢 Pronto para promoção.' },
  eval: { status: 'advisory', summary: '🟡 eval indisponível' },
  allowlist_violations: [],
  consumer_group: 'account users',
  timeline: [
    { key: 'checks', label: 'Checagens determinísticas (pré-render + allowlist)', status: 'pass' },
    { key: 'review', label: 'Revisão do agente (Genie Reviewer)', status: 'pass' },
    { key: 'eval', label: 'Eval-run (advisory)', status: 'pass' },
    { key: 'approval', label: 'Aprovação do Steward', status: 'running' },
    { key: 'deploy', label: 'Deploy em produção (service principal)', status: 'pending' },
  ],
};

const blockerReview = {
  ...cleanReview,
  findings: [{ rule_id: 'EVAL-01', severity: 'BLOCKER', message: 'Poucas perguntas de benchmark.' }],
  gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 Promoção bloqueada.' },
  timeline: cleanReview.timeline.map((t) => (t.key === 'review' ? { ...t, status: 'fail' } : t)),
};

// Distinct requester (malcoln) and steward (pedro) — the SoD precondition.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'pedro@databricks.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Recebíveis' }] } }),
  );
});

async function reviewOnce(page: import('@playwright/test').Page, review: unknown) {
  await page.route('**/api/review', (route) => route.fulfill({ json: review }));
  await page.goto('/');
  await page.getByLabel('Recurso').selectOption({ label: 'Recebíveis' });
  await page.getByRole('button', { name: /Solicitar promoção/ }).click();
  await expect(page.getByRole('heading', { name: 'Aprovação do Steward' })).toBeVisible();
}

test('author cannot approve their own promotion (SoD)', async ({ page }) => {
  await reviewOnce(page, cleanReview);
  // Persona defaults to Autor: SoD message, no approve button.
  await expect(page.getByText(/não pode aprovar a própria promoção/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Aprovar promoção/ })).toHaveCount(0);
});

test('steward is blocked from approving while a BLOCKER finding stands', async ({ page }) => {
  await reviewOnce(page, blockerReview);
  await page.getByRole('button', { name: 'Steward' }).click();
  await expect(page.getByText(/bloqueada por achados BLOCKER/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Aprovar promoção/ })).toHaveCount(0);
});

test('a distinct steward can approve a clean review and it advances the timeline', async ({
  page,
}) => {
  await reviewOnce(page, cleanReview);
  await page.getByRole('button', { name: 'Steward' }).click();

  const approveBtn = page.getByRole('button', { name: /Aprovar promoção/ });
  await expect(approveBtn).toBeVisible();

  // Before approval, the deploy step is pending.
  const deployRow = page.getByRole('listitem').filter({ hasText: 'Deploy em produção' });
  await expect(deployRow).toContainText('pendente');

  await approveBtn.click();

  await expect(page.getByText(/Aprovado pelo Steward/)).toBeVisible();
  // The approval + deploy rows advance to concluído.
  await expect(deployRow).toContainText('concluído');
});

test('a steward configured as the requester (misconfig) still cannot self-approve (SoD)', async ({
  page,
}) => {
  // APP_STEWARD === the OBO user → the SoD guard must still block.
  await page.unroute('**/api/whoami');
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'malcoln@databricks.com', steward: 'malcoln@databricks.com' } }),
  );
  await reviewOnce(page, cleanReview);
  await page.getByRole('button', { name: 'Steward' }).click();

  await expect(page.getByText(/o solicitante não pode aprovar a própria promoção/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Aprovar promoção/ })).toHaveCount(0);
});

test('an approval persists across a persona toggle (a decision, not view state)', async ({
  page,
}) => {
  await reviewOnce(page, cleanReview);
  await page.getByRole('button', { name: 'Steward' }).click();
  await page.getByRole('button', { name: /Aprovar promoção/ }).click();
  await expect(page.getByText(/Aprovado pelo Steward/)).toBeVisible();

  // Toggling back to Autor keeps the approval fact visible (it doesn't reset).
  await page.getByRole('button', { name: 'Autor' }).click();
  await expect(page.getByText(/Aprovado pelo Steward/)).toBeVisible();
});

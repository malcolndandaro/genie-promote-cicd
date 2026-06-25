import { test, expect } from '@playwright/test';
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';

// ── Tests ───────────────────────────────────────────────────────────────────
// This app uses no AppKit data plugins (custom Express proxy to the engine API), so there are
// no per-plugin pages to smoke — just the home screen's static UI.

let testArtifactsDir: string;
let consoleLogs: string[] = [];
let consoleErrors: string[] = [];
let pageErrors: string[] = [];
let failedRequests: string[] = [];

test('smoke test - app loads and displays home page', async ({ page }) => {
  await page.goto('/');

  // Static UI (renders regardless of the engine API / OBO — the local smoke has no user token).
  await expect(page.getByRole('heading', { name: 'Promotor de Genie/AI-BI' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Meus espaços' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '＋ Novo Genie Space' })).toBeVisible();

  await expect(page.getByRole('link', { name: 'Home' })).toBeVisible();
});

test('smoke test - review renders findings + AI-trust', async ({ page }) => {
  // Mock the proxy endpoints (the local smoke has no engine / OBO token).
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'tester@example.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Test Space' }] } }),
  );
  await page.route('**/api/review', (route) =>
    route.fulfill({
      json: {
        findings: [
          {
            rule_id: 'EVAL-01',
            severity: 'BLOCKER',
            message: 'Apenas 2 pergunta(s) de benchmark; produção exige >= 3.',
            citation: 'Genie Promotion Handbook › Quality › EVAL-01',
            suggestion: 'Adicionar perguntas de benchmark.',
          },
        ],
        gate: { conclusion: 'failure', blocker_count: 1, summary: '🔴 1 de 1 achado(s) são BLOCKER — promoção bloqueada.' },
        eval: { status: 'advisory', summary: '🟡 eval-run advisory' },
        timeline: [
          { key: 'checks', label: 'Checagens determinísticas', status: 'pass' },
          { key: 'review', label: 'Revisão do agente', status: 'fail' },
        ],
        allowlist_violations: [],
        consumer_group: 'account users',
      },
    }),
  );

  await page.goto('/');
  await page.getByRole('combobox', { name: 'Espaço' }).click();
  await page.getByRole('option', { name: 'Test Space' }).click();
  await page.getByRole('button', { name: 'Solicitar promoção →' }).click();

  // The full Revisão panel: a finding card, the gate verdict, and the persistent AI disclaimer.
  await expect(page.getByText('EVAL-01', { exact: true })).toBeVisible();
  await expect(page.getByText('BLOCKER', { exact: true })).toBeVisible();
  await expect(page.getByText('Achados gerados por IA — verifique antes de aprovar.')).toBeVisible();
  await expect(page.getByText('promoção bloqueada', { exact: false })).toBeVisible();

  // SoD: even as the Steward, a promotion with a BLOCKER cannot be approved.
  await page.getByRole('combobox', { name: 'Persona' }).click();
  await page.getByRole('option', { name: 'Steward' }).click();
  await expect(page.getByText('resolva (ex.: /genie-fix) antes de aprovar', { exact: false })).toBeVisible();
});

test('smoke test - steward approves a clean review (SoD)', async ({ page }) => {
  await page.route('**/api/whoami', (route) =>
    route.fulfill({ json: { email: 'author@example.com', steward: 'steward@example.com' } }),
  );
  await page.route('**/api/spaces', (route) =>
    route.fulfill({ json: { spaces: [{ space_id: 'sp1', title: 'Test Space' }] } }),
  );
  await page.route('**/api/review', (route) =>
    route.fulfill({
      json: {
        findings: [],
        gate: { conclusion: 'success', blocker_count: 0, summary: '✅ Sem achados.' },
        eval: { status: 'advisory', summary: '🟡 advisory' },
        timeline: [
          { key: 'checks', label: 'Checagens', status: 'pass' },
          { key: 'review', label: 'Revisão', status: 'pass' },
          { key: 'eval', label: 'Eval-run', status: 'pass' },
          { key: 'approval', label: 'Aprovação do Steward', status: 'running' },
          { key: 'deploy', label: 'Deploy em produção', status: 'pending' },
        ],
        allowlist_violations: [],
        consumer_group: 'account users',
      },
    }),
  );

  await page.goto('/');
  await page.getByRole('combobox', { name: 'Persona' }).click();
  await page.getByRole('option', { name: 'Steward' }).click();
  await page.getByRole('combobox', { name: 'Espaço' }).click();
  await page.getByRole('option', { name: 'Test Space' }).click();
  await page.getByRole('button', { name: 'Solicitar promoção →' }).click();

  // A distinct Steward (≠ requester) can approve a clean review; approval advances the timeline.
  const approve = page.getByRole('button', { name: '✔ Aprovar promoção' });
  await expect(approve).toBeVisible();
  await approve.click();
  await expect(page.getByText('Aprovado pelo Steward', { exact: false })).toBeVisible();
});

// ── Lifecycle hooks ─────────────────────────────────────────────────────────

test.beforeEach(async ({ page }) => {
  consoleLogs = [];
  consoleErrors = [];
  pageErrors = [];
  failedRequests = [];

  // Create temp directory for test artifacts
  testArtifactsDir = join(process.cwd(), '.smoke-test');
  mkdirSync(testArtifactsDir, { recursive: true });

  // Capture console logs and errors (including React errors)
  page.on('console', (msg) => {
    const type = msg.type();
    const text = msg.text();

    // Skip empty lines and formatting placeholders
    if (!text.trim() || /^%[osd]$/.test(text.trim())) {
      return;
    }

    // Get stack trace for errors if available
    const location = msg.location();
    const locationStr = location.url ? ` at ${location.url}:${location.lineNumber}:${location.columnNumber}` : '';

    consoleLogs.push(`[${type}] ${text}${locationStr}`);

    // Separately track error messages (React errors appear here)
    if (type === 'error') {
      consoleErrors.push(`${text}${locationStr}`);
    }
  });

  // Capture page errors with full stack trace
  page.on('pageerror', (error) => {
    const errorDetails = `Page error: ${error.message}\nStack: ${error.stack || 'No stack trace available'}`;
    pageErrors.push(errorDetails);
    // Also log to console for immediate visibility
    console.error('Page error detected:', errorDetails);
  });

  // Capture failed requests
  page.on('requestfailed', (request) => {
    failedRequests.push(`Failed request: ${request.url()} - ${request.failure()?.errorText}`);
  });
});

test.afterEach(async ({ page }, testInfo) => {
  const testName = testInfo.title.replace(/ /g, '-').toLowerCase();
  // Always capture artifacts, even if test fails
  const screenshotPath = join(testArtifactsDir, `${testName}-app-screenshot.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });

  const logsPath = join(testArtifactsDir, `${testName}-console-logs.txt`);
  const allLogs = [
    '=== Console Logs ===',
    ...consoleLogs,
    '\n=== Console Errors (React errors) ===',
    ...consoleErrors,
    '\n=== Page Errors ===',
    ...pageErrors,
    '\n=== Failed Requests ===',
    ...failedRequests,
  ];
  writeFileSync(logsPath, allLogs.join('\n'), 'utf-8');

  console.log(`Screenshot saved to: ${screenshotPath}`);
  console.log(`Console logs saved to: ${logsPath}`);
  if (consoleErrors.length > 0) {
    console.log('Console errors detected:', consoleErrors);
  }
  if (pageErrors.length > 0) {
    console.log('Page errors detected:', pageErrors);
  }
  if (failedRequests.length > 0) {
    console.log('Failed requests detected:', failedRequests);
  }

  await page.close();
});

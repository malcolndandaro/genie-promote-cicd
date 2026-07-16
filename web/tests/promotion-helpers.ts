import { expect, type Page } from '@playwright/test';


/** Select the required Público do Space in tests whose concern is downstream of confirmation. */
export async function selectPilotAudience(page: Page): Promise<void> {
  await page.route('**/api/principals**', (route) =>
    route.fulfill({
      json: {
        principals: [
          {
            type: 'user',
            id: 'pilot-user',
            display: 'Usuária Piloto',
            email: 'pilot.user@databricks.com',
          },
        ],
      },
    }),
  );
  const picker = page.getByRole('combobox', { name: 'Usuário ou grupo' });
  await picker.click();
  const option = page.getByRole('option', { name: /Usuária Piloto/ });
  await expect(option).toBeVisible();
  // The reusable picker is fixed-position so a deeply scrolled confirmation card cannot clip it.
  // Browser layout may still place the fixed panel below Playwright's synthetic viewport; dispatch
  // the same DOM click after visibility has proven that the result loaded.
  await option.evaluate((element) => (element as HTMLElement).click());
  await expect(page.getByRole('button', { name: 'Confirmar promoção' })).toBeEnabled();
}


export async function confirmPilotPromotion(page: Page): Promise<void> {
  await selectPilotAudience(page);
  await page.getByRole('button', { name: 'Confirmar promoção' }).click();
}

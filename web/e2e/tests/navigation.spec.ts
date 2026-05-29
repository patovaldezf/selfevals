import { test, expect } from '@playwright/test';
import { WORKSPACE_ID, gotoOk } from '../helpers';

/**
 * Navigation flow: drive the app the way a user does — click through
 * from the workspace list down to an experiment, and across the sidebar
 * nav — instead of deep-linking. This catches broken hrefs and client
 * router regressions that direct `page.goto` would miss.
 */

test.describe('navigation: click-through flows', () => {
  test('list → workspace → experiment detail', async ({ page }) => {
    await gotoOk(page, '/');

    // Enter the (only) workspace.
    await page.locator(`a[href="/${WORKSPACE_ID}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/${WORKSPACE_ID}$`));
    await expect(page.getByRole('heading', { name: 'Recent experiments' })).toBeVisible();

    // Drill into the first experiment row.
    await page.locator(`a[href^="/${WORKSPACE_ID}/experiments/"]`).first().click();
    await expect(page).toHaveURL(new RegExp(`/${WORKSPACE_ID}/experiments/exp_`));
    await expect(page.getByRole('heading', { name: 'pingpong baseline' })).toBeVisible();
  });

  test('sidebar nav reaches every section', async ({ page }) => {
    await gotoOk(page, `/${WORKSPACE_ID}`);

    for (const [label, urlPart] of [
      ['Experiments', '/experiments'],
      ['Anchor set', '/anchor-set'],
      ['Clusters', '/clusters'],
      ['Datasets', '/datasets'],
      ['Overview', `/${WORKSPACE_ID}`]
    ] as const) {
      await page.getByRole('link', { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(urlPart.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      await expect(page.getByText('Backend unreachable')).toHaveCount(0);
    }
  });
});

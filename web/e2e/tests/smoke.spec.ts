import { test, expect } from '@playwright/test';
import { WORKSPACE_ID, firstExperimentId, gotoOk } from '../helpers';

/**
 * Smoke suite: every primary route renders against the real backend
 * without hitting the "Backend unreachable" fallback or a client error.
 * This is the cheapest possible regression net — if a +page.server.ts
 * loader or the SSR /api proxy breaks, one of these fails immediately.
 */

test.describe('smoke: routes render with real data', () => {
  test('workspace list renders the seeded workspace', async ({ page }) => {
    await gotoOk(page, '/');
    await expect(page.getByRole('heading', { name: 'Workspaces' })).toBeVisible();
    // The seed produces exactly one workspace; its tile links to /:wsId.
    await expect(page.locator(`a[href="/${WORKSPACE_ID}"]`)).toBeVisible();
  });

  test('workspace overview renders experiments section', async ({ page }) => {
    await gotoOk(page, `/${WORKSPACE_ID}`);
    await expect(page.getByRole('heading', { name: 'Recent experiments' })).toBeVisible();
    // Seeded experiment name comes straight from example_pingpong.yaml.
    await expect(page.getByText('pingpong baseline').first()).toBeVisible();
  });

  test('experiments index renders', async ({ page }) => {
    await gotoOk(page, `/${WORKSPACE_ID}/experiments`);
    await expect(page.getByRole('heading', { name: 'Experiments' })).toBeVisible();
  });

  test('anchor-set, clusters, datasets all render', async ({ page }) => {
    for (const [path, heading] of [
      [`/${WORKSPACE_ID}/anchor-set`, 'Anchor set'],
      [`/${WORKSPACE_ID}/clusters`, 'Clusters'],
      [`/${WORKSPACE_ID}/datasets`, 'Datasets']
    ] as const) {
      await gotoOk(page, path);
      await expect(
        page.getByRole('heading', { name: heading, exact: false }).first()
      ).toBeVisible();
    }
  });

  test('experiment detail renders for the seeded experiment', async ({ page, request }) => {
    const expId = await firstExperimentId(request);
    await gotoOk(page, `/${WORKSPACE_ID}/experiments/${expId}`);
    await expect(page.getByRole('heading', { name: 'pingpong baseline' })).toBeVisible();
  });

  test('no uncaught console errors on the overview page', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await gotoOk(page, `/${WORKSPACE_ID}`);
    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([]);
  });
});

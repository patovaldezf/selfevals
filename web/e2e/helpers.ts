import { expect, type Page, type APIRequestContext } from '@playwright/test';

/**
 * Shared E2E helpers.
 *
 * Experiment / trace / iteration ids are ULIDs minted fresh on every
 * seed run, so tests must NOT hardcode them — they discover ids at
 * runtime by asking the API (fast, no rendering) or by following links
 * in the UI (exercises the real navigation). Only the workspace id is
 * stable: it comes from the spec's `workspace:` key.
 */

/** The workspace id seeded by example_pingpong.yaml — stable across runs. */
export const WORKSPACE_ID = 'ws_01HZZZZZZZZZZZZZZZZZZZZZZZ';

export const API_BASE = `http://127.0.0.1:${process.env.E2E_API_PORT ?? 8000}`;

/** Fetch the first experiment id for the seeded workspace, via the API. */
export async function firstExperimentId(request: APIRequestContext): Promise<string> {
  const res = await request.get(`${API_BASE}/api/workspaces/${WORKSPACE_ID}/experiments`);
  expect(res.ok(), `experiments endpoint should be 2xx (got ${res.status()})`).toBeTruthy();
  const body = await res.json();
  const items = body.items ?? [];
  expect(items.length, 'seed should produce at least one experiment').toBeGreaterThan(0);
  return items[0].id as string;
}

/** Navigate to a path and assert it didn't render the "Backend unreachable" state. */
export async function gotoOk(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(
    page.getByText('Backend unreachable'),
    `"${path}" rendered the backend-unreachable error — is the API up and seeded?`
  ).toHaveCount(0);
}

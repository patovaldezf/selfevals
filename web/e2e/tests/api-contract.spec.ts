import { test, expect } from '@playwright/test';
import { WORKSPACE_ID, API_BASE, firstExperimentId } from '../helpers';

/**
 * API-contract checks: hit the FastAPI bridge directly (no browser) to
 * pin the JSON shapes the FE's typed client in src/lib/api/client.ts
 * mirrors by hand. If the backend renames a field, these fail before a
 * page does — a faster, clearer signal than a rendering assertion.
 *
 * These also implicitly verify the seed produced the expected data.
 */

test.describe('api contract', () => {
  test('GET /api/health', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/health`);
    expect(res.ok()).toBeTruthy();
    expect(await res.json()).toMatchObject({ status: expect.any(String) });
  });

  test('GET /api/workspaces returns the seeded workspace', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/workspaces`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(Array.isArray(body.workspaces)).toBeTruthy();
    const ws = body.workspaces.find((w: { id: string }) => w.id === WORKSPACE_ID);
    expect(ws, `workspace ${WORKSPACE_ID} should be present`).toBeTruthy();
    expect(ws).toMatchObject({
      id: expect.any(String),
      slug: expect.any(String),
      name: expect.any(String),
      experiment_count: expect.any(Number)
    });
  });

  test('GET experiments index has pagination envelope', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/workspaces/${WORKSPACE_ID}/experiments`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toMatchObject({
      items: expect.any(Array),
      total: expect.any(Number),
      limit: expect.any(Number),
      offset: expect.any(Number),
      has_more: expect.any(Boolean)
    });
    expect(body.items.length).toBeGreaterThan(0);
  });

  test('GET experiment detail exposes core fields', async ({ request }) => {
    const expId = await firstExperimentId(request);
    const res = await request.get(
      `${API_BASE}/api/workspaces/${WORKSPACE_ID}/experiments/${expId}`
    );
    expect(res.ok()).toBeTruthy();
    // Detail nests the experiment under `summary` (see ExperimentDetail in
    // src/lib/api/client.ts) — not a flat object.
    expect(await res.json()).toMatchObject({
      summary: {
        id: expId,
        name: expect.any(String),
        primary_metric: expect.any(String),
        primary_target: { operator: expect.any(String), value: expect.any(Number) }
      }
    });
  });

  test('unknown workspace returns 404', async ({ request }) => {
    const res = await request.get(`${API_BASE}/api/workspaces/ws_does_not_exist`);
    expect(res.status()).toBe(404);
  });
});

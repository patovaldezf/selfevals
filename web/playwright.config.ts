import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * E2E config: real backend + real web build.
 *
 * Topology (mirrors `selfevals serve`):
 *
 *   FastAPI  :8000  ── reads ──▶  e2e/.fixtures/e2e.sqlite
 *      ▲
 *      │  /api/* proxied by hooks.server.ts (SELFEVALS_API_BASE)
 *      │
 *   SvelteKit preview :4173  ◀── Playwright drives this
 *
 * Playwright's `webServer` boots BOTH. `globalSetup` seeds the fixture
 * db first (via e2e/fixtures/seed.sh) so the API has real data to serve.
 *
 * Ports are deliberately off the dev defaults (8000→API is fine, but the
 * web uses 4173 not 5173) so an `npm run dev` session can run alongside
 * the E2E suite without a port clash.
 */

const API_PORT = Number(process.env.E2E_API_PORT ?? 8000);
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 4173);
// Postgres-only: the backend reads from a Postgres database seeded by
// global-setup (e2e/fixtures/seed.sh). Defaults to the docker-compose instance
// on :5433 locally; CI overrides E2E_DB_URL to its service.
const FIXTURE_DB_URL =
  process.env.E2E_DB_URL ?? 'postgresql://selfevals:selfevals@localhost:5433/selfevals';

// The repo-local uv venv, used to launch the API. Overridable for CI.
const SELFEVALS_PYTHON = process.env.SELFEVALS_PYTHON ?? resolve(__dirname, '../.venv/bin/python');

export default defineConfig({
  testDir: './e2e/tests',
  // One fixture db shared read-only across tests → safe to parallelize.
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  globalSetup: './e2e/global-setup.ts',

  timeout: 30_000,
  expect: { timeout: 7_000 },

  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure'
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] }
    }
  ],

  webServer: [
    {
      // FastAPI backend, pointed at the seeded Postgres fixture database.
      command: `${SELFEVALS_PYTHON} -m selfevals.api --host 127.0.0.1 --port ${API_PORT} --db ${FIXTURE_DB_URL}`,
      cwd: resolve(__dirname, '..'),
      port: API_PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe'
    },
    {
      // SvelteKit adapter-node SERVER (not `vite preview` — preview only
      // serves the client bundle and never runs hooks.server.ts, so the
      // /api proxy would be dead). We build, then boot `node build/index.js`,
      // which is exactly what `selfevals serve` spawns in production.
      command: `npm run build && node build/index.js`,
      cwd: __dirname,
      port: WEB_PORT,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        HOST: '127.0.0.1',
        PORT: String(WEB_PORT),
        // adapter-node enforces same-origin POST/CSRF checks against ORIGIN.
        ORIGIN: `http://127.0.0.1:${WEB_PORT}`,
        // hooks.server.ts forwards /api/* here when this is set.
        SELFEVALS_API_BASE: `http://127.0.0.1:${API_PORT}`
      }
    }
  ]
});

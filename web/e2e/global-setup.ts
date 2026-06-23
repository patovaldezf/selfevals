import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import type { FullConfig } from '@playwright/test';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Runs once before the whole E2E suite (and before `webServer` boots, so
 * the API has a populated db the moment it starts).
 *
 * Delegates the actual seeding to e2e/fixtures/seed.sh — keeping the
 * "how do I populate a selfevals db" knowledge in one shell script that
 * a human can also run by hand for local debugging:
 *
 *     ./e2e/fixtures/seed.sh
 */
async function globalSetup(_config: FullConfig) {
  const script = resolve(__dirname, 'fixtures/seed.sh');
  if (!existsSync(script)) {
    throw new Error(`global-setup: seed script not found at ${script}`);
  }

  const dbUrl =
    process.env.E2E_DB_URL ?? 'postgresql://selfevals:selfevals@localhost:5433/selfevals';
  console.log(`\n[e2e] seeding Postgres fixture db → ${dbUrl}`);

  try {
    execFileSync('bash', [script], {
      stdio: 'inherit',
      env: { ...process.env, E2E_DB_URL: dbUrl }
    });
  } catch (err) {
    throw new Error(
      `global-setup: seeding failed. Ensure the selfevals Python venv exists ` +
        `(repo ../.venv) or set SELFEVALS_PYTHON, and that Postgres is reachable ` +
        `at E2E_DB_URL. Original error: ${
          err instanceof Error ? err.message : String(err)
        }`
    );
  }
}

export default globalSetup;

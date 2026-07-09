import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { existsSync } from 'node:fs';
import type { FullConfig } from '@playwright/test';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Runs once before the whole E2E suite (and before `webServer` boots, so
 * the API has a populated db the moment it starts).
 *
 * `selfevals run` shards execution onto a Redis-backed job queue — it
 * enqueues a run-job and blocks until a worker drains it (see
 * cli/commands.py::cmd_run). So before seeding we start one `selfevals
 * worker runs` process in the background, pointed at the same Redis +
 * Postgres the seed script uses. It keeps running for the whole test
 * session (in case a test itself triggers a run through the UI) and is
 * killed in global-teardown.ts.
 *
 * The actual seeding is delegated to e2e/fixtures/seed.sh — keeping the
 * "how do I populate a selfevals db" knowledge in one shell script that
 * a human can also run by hand for local debugging:
 *
 *     ./e2e/fixtures/seed.sh
 */
async function globalSetup(_config: FullConfig) {
  const dbUrl =
    process.env.E2E_DB_URL ?? 'postgresql://selfevals:selfevals@localhost:5433/selfevals';
  const redisUrl = process.env.SELFEVALS_REDIS_URL ?? 'redis://localhost:6380/0';

  const python = process.env.SELFEVALS_PYTHON ?? resolve(__dirname, '../../.venv/bin/python');
  const workerLog = process.env.CI ? 'inherit' : 'ignore';
  // `--db` is a global flag (`selfevals --db ... worker runs`), not a
  // `worker runs` option — it must precede the subcommand.
  const worker = spawn(python, ['-m', 'selfevals.cli.main', '--db', dbUrl, 'worker', 'runs'], {
    env: { ...process.env, SELFEVALS_REDIS_URL: redisUrl },
    stdio: ['ignore', workerLog, workerLog],
    detached: true
  });
  worker.unref();
  process.env.__SELFEVALS_E2E_WORKER_PID = String(worker.pid);
  // Give the worker a moment to connect to Redis before the seed run enqueues
  // its job — otherwise the first job can sit unclaimed until the worker's
  // consumer-group read picks it up on its next poll.
  await new Promise((r) => setTimeout(r, 500));

  const script = resolve(__dirname, 'fixtures/seed.sh');
  if (!existsSync(script)) {
    throw new Error(`global-setup: seed script not found at ${script}`);
  }

  console.log(`\n[e2e] seeding Postgres fixture db → ${dbUrl}`);

  try {
    execFileSync('bash', [script], {
      stdio: 'inherit',
      env: { ...process.env, E2E_DB_URL: dbUrl, SELFEVALS_REDIS_URL: redisUrl }
    });
  } catch (err) {
    throw new Error(
      `global-setup: seeding failed. Ensure the selfevals Python venv exists ` +
        `(repo ../.venv) or set SELFEVALS_PYTHON, that Postgres is reachable ` +
        `at E2E_DB_URL, and that Redis is reachable at SELFEVALS_REDIS_URL ` +
        `(\`selfevals run\` requires a worker draining it — see cli/commands.py). ` +
        `Original error: ${err instanceof Error ? err.message : String(err)}`
    );
  }
}

export default globalSetup;

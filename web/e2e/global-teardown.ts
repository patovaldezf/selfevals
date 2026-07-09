/**
 * Kills the `selfevals worker runs` process started in global-setup.ts.
 *
 * The worker is spawned detached so it survives Playwright's own process
 * tree management; without this it would leak past the E2E run (harmless
 * in CI, which tears the whole runner down, but annoying for local
 * `npm run test:e2e` where it'd keep polling Redis forever).
 */
async function globalTeardown() {
  const pid = process.env.__SELFEVALS_E2E_WORKER_PID;
  if (!pid) return;
  try {
    process.kill(-Number(pid), 'SIGTERM');
  } catch {
    // Already gone, or not a process group leader on this platform — fine.
  }
}

export default globalTeardown;

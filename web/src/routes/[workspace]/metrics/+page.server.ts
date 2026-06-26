import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

/** Default window: last 7 days. The picker writes `?from=&to=` (ISO date) and
 *  the load re-runs server-side. We pass the raw strings straight to the API,
 *  which parses them (`from`/`to` query aliases). All six metrics load in
 *  parallel; one failing endpoint doesn't blank the whole dashboard. */
const WINDOW_DAYS = 7;

function defaultRange(): { from: string; to: string } {
  // No Date.now() in the harness, but this runs in the SvelteKit server at
  // request time (real Node), so the real clock is available here.
  const now = new Date();
  const from = new Date(now.getTime() - WINDOW_DAYS * 24 * 60 * 60 * 1000);
  return { from: from.toISOString(), to: now.toISOString() };
}

export const load: PageServerLoad = async ({ fetch, params, url }) => {
  const def = defaultRange();
  const from = url.searchParams.get('from') ?? def.from;
  const to = url.searchParams.get('to') ?? def.to;
  const ws = params.workspace;
  const opts = { from, to };

  const settle = <T>(p: Promise<T>) =>
    p.then(
      (value) => ({ ok: true as const, value }),
      (err) => ({ ok: false as const, error: err?.detail ?? String(err) })
    );

  const [passRate, failureModes, tools, cost, tokens, latency] = await Promise.all([
    settle(api.metricsPassRate(ws, opts, fetch)),
    settle(api.metricsFailureModes(ws, opts, fetch)),
    settle(api.metricsTools(ws, opts, fetch)),
    settle(api.metricsCost(ws, opts, fetch)),
    settle(api.metricsTokens(ws, opts, fetch)),
    settle(api.metricsLatency(ws, opts, fetch))
  ]);

  return { from, to, passRate, failureModes, tools, cost, tokens, latency };
};

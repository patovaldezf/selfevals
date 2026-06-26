import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

/** Failure clusters default to the full history — unlike the metrics dashboard,
 *  a blocker that hasn't recurred in a week is still worth seeing. An optional
 *  `?experiment_id=` narrows to one experiment. The load settles so a backend
 *  hiccup renders an honest error state instead of a 500 page. */
export const load: PageServerLoad = async ({ fetch, params, url }) => {
  const ws = params.workspace;
  const experiment_id = url.searchParams.get('experiment_id') ?? undefined;

  const clusters = await api.clusters(ws, { experiment_id }, fetch).then(
    (value) => ({ ok: true as const, value }),
    (err) => ({ ok: false as const, error: err?.detail ?? String(err) })
  );

  return { clusters, experimentId: experiment_id ?? null };
};

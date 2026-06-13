import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  // A8: same pagination envelope as the workspace overview. The
  // experiments index shows "X of N" in the header so a user with
  // more than a page knows there's more to load.
  // Datasets are loaded alongside so the "Run experiment" form can offer them
  // as an override; a failure there must not block the experiments list.
  const [page, datasets] = await Promise.all([
    api.listExperiments(params.workspace, fetch),
    api.listDatasets(params.workspace, fetch).then((d) => d.items).catch(() => [])
  ]);
  return {
    experiments: page.items,
    experimentsTotal: page.total,
    experimentsHasMore: page.has_more,
    experimentsLimit: page.limit,
    datasets
  };
};

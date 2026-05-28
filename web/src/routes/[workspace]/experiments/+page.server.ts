import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  // A8: same pagination envelope as the workspace overview. The
  // experiments index shows "X of N" in the header so a user with
  // more than a page knows there's more to load.
  const page = await api.listExperiments(params.workspace, fetch);
  return {
    experiments: page.items,
    experimentsTotal: page.total,
    experimentsHasMore: page.has_more,
    experimentsLimit: page.limit
  };
};

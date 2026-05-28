import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  // A8: experiments comes back as a pagination envelope. The workspace
  // overview only shows the first page; the "Recent experiments" header
  // surfaces "X of N" when the page doesn't cover everything.
  const [page, anchor] = await Promise.all([
    api.listExperiments(params.workspace, fetch),
    api.anchorSet(params.workspace, fetch).catch(() => [])
  ]);
  return {
    experiments: page.items,
    experimentsTotal: page.total,
    experimentsHasMore: page.has_more,
    anchor
  };
};

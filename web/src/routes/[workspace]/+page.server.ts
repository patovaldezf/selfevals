import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  const [experiments, anchor] = await Promise.all([
    api.listExperiments(params.workspace, fetch),
    api.anchorSet(params.workspace, fetch).catch(() => [])
  ]);
  return { experiments, anchor };
};

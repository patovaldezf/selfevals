import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
  const all = url.searchParams.get('all') === 'true';
  const bundle = await api.analysisBundle(params.workspace, params.experiment, { all }, fetch);
  return { bundle, all, experimentId: params.experiment };
};

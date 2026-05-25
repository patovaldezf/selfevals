import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  const experiments = await api.listExperiments(params.workspace, fetch);
  return { experiments };
};

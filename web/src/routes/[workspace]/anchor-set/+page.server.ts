import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  const points = await api.anchorSet(params.workspace, fetch);
  return { points };
};

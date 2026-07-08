import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  const arenas = await api.listArenas(params.workspace, fetch);
  return { arenas };
};

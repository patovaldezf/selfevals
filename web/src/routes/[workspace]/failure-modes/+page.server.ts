import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
  const status = url.searchParams.get('status') ?? undefined;
  const { items } = await api.listFailureModes(params.workspace, { status }, fetch);
  return { modes: items, status: status ?? '' };
};

import { api } from '$lib/api/client';
import { error } from '@sveltejs/kit';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ fetch, params }) => {
  try {
    const workspace = await api.workspace(params.workspace, fetch);
    return { workspace };
  } catch (err) {
    if (
      typeof err === 'object' &&
      err !== null &&
      'status' in err &&
      (err as { status: number }).status === 404
    ) {
      throw error(404, 'Workspace not found');
    }
    throw err;
  }
};

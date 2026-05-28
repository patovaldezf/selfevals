import { api } from '$lib/api/client';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  try {
    const thread = await api.thread(params.workspace, params.thread, fetch);
    return { thread };
  } catch (err) {
    if (
      typeof err === 'object' &&
      err !== null &&
      'status' in err &&
      (err as { status: number }).status === 404
    ) {
      throw error(404, 'Thread not found');
    }
    throw err;
  }
};

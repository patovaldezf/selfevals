import { api } from '$lib/api/client';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  try {
    const [detail, decisions] = await Promise.all([
      api.experiment(params.workspace, params.experiment, fetch),
      api.decisions(params.workspace, params.experiment, fetch).catch(() => [])
    ]);
    return { detail, decisions };
  } catch (err) {
    if (
      typeof err === 'object' &&
      err !== null &&
      'status' in err &&
      (err as { status: number }).status === 404
    ) {
      throw error(404, 'Experiment not found');
    }
    throw err;
  }
};

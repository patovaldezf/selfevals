import { api } from '$lib/api/client';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  try {
    const [detail, decisions, datasets] = await Promise.all([
      api.experiment(params.workspace, params.experiment, fetch),
      api.decisions(params.workspace, params.experiment, fetch).catch(() => []),
      // Datasets power the baseline/regression actions in the iteration drawer.
      api
        .listDatasets(params.workspace, fetch)
        .then((d) => d.items)
        .catch(() => [])
    ]);
    return { detail, decisions, datasets };
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

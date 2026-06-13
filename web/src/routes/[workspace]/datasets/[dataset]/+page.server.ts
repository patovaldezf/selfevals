import { api } from '$lib/api/client';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  try {
    const dataset = await api.dataset(params.workspace, params.dataset, fetch);
    // The baseline is the regression anchor. 404 = none set yet (normal), so
    // it's optional data, not an error for the page.
    const baseline = await api
      .getBaseline(params.workspace, params.dataset, fetch)
      .catch(() => null);
    return { dataset, baseline };
  } catch (err) {
    if (
      typeof err === 'object' &&
      err !== null &&
      'status' in err &&
      (err as { status: number }).status === 404
    ) {
      throw error(404, 'Dataset not found');
    }
    throw err;
  }
};

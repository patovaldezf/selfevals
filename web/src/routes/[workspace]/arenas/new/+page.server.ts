import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  // Datasets are offered as a picker so the arena can reference an existing
  // persisted dataset instead of requiring the user to hand-write cases_inline.
  const datasets = await api
    .listDatasets(params.workspace, fetch)
    .then((d) => d.items)
    .catch(() => []);
  return { datasets };
};

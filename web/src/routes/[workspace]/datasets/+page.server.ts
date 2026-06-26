import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params, url }) => {
  const status = url.searchParams.get('status') ?? undefined;
  const dataset_type = url.searchParams.get('type') ?? undefined;
  const page = await api.listDatasets(params.workspace, fetch, { status, dataset_type });
  return {
    datasets: page.items,
    total: page.total,
    hasMore: page.has_more,
    status: status ?? '',
    datasetType: dataset_type ?? ''
  };
};

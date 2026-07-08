import { qs, request } from '$lib/api/http';
import type {
  AppendDatasetCaseResult,
  Baseline,
  CreateDatasetRequest,
  DatasetDetail,
  DatasetListPage,
  RegressionResult
} from '$lib/api/types';

export const datasetsApi = {
  listDatasets: (
    workspaceId: string,
    fetch?: typeof globalThis.fetch,
    options: { limit?: number; offset?: number; status?: string; dataset_type?: string } = {}
  ) =>
    request<DatasetListPage>(
      `/api/workspaces/${workspaceId}/datasets${qs({
        limit: options.limit,
        offset: options.offset,
        status: options.status,
        dataset_type: options.dataset_type
      })}`,
      { fetch }
    ),

  dataset: (workspaceId: string, datasetId: string, fetch?: typeof globalThis.fetch) =>
    request<DatasetDetail>(`/api/workspaces/${workspaceId}/datasets/${datasetId}`, { fetch }),

  createDataset: (
    workspaceId: string,
    body: CreateDatasetRequest,
    fetch?: typeof globalThis.fetch
  ) =>
    request<DatasetDetail>(`/api/workspaces/${workspaceId}/datasets`, {
      method: 'POST',
      json: body,
      fetch
    }),

  /** Upload a `.jsonl` file as a new dataset (multipart). */
  uploadDataset: (workspaceId: string, form: FormData, fetch?: typeof globalThis.fetch) =>
    request<DatasetDetail>(`/api/workspaces/${workspaceId}/datasets/upload`, {
      method: 'POST',
      form,
      fetch
    }),

  /** Freeze a dataset (irreversible — recomputes the manifest hash). */
  freezeDataset: (workspaceId: string, datasetId: string, fetch?: typeof globalThis.fetch) =>
    request<DatasetDetail>(`/api/workspaces/${workspaceId}/datasets/${datasetId}/freeze`, {
      method: 'POST',
      fetch
    }),

  appendDatasetCase: (
    workspaceId: string,
    datasetId: string,
    body: { case: Record<string, unknown>; create_version_if_frozen?: boolean },
    fetch?: typeof globalThis.fetch
  ) =>
    request<AppendDatasetCaseResult>(`/api/workspaces/${workspaceId}/datasets/${datasetId}/cases`, {
      method: 'POST',
      json: body,
      fetch
    }),

  // --- Baseline & regression (2B) --------------------------------------

  getBaseline: (workspaceId: string, datasetId: string, fetch?: typeof globalThis.fetch) =>
    request<Baseline>(`/api/workspaces/${workspaceId}/datasets/${datasetId}/baseline`, { fetch }),

  setBaseline: (
    workspaceId: string,
    datasetId: string,
    iterationId: string | null,
    fetch?: typeof globalThis.fetch
  ) =>
    request<Baseline>(`/api/workspaces/${workspaceId}/datasets/${datasetId}/baseline`, {
      method: 'PUT',
      json: { iteration_id: iterationId ?? null },
      fetch
    }),

  regressionCheck: (
    workspaceId: string,
    datasetId: string,
    body: {
      iteration_id: string;
      primary_drop?: number;
      per_class_f1_drop?: number;
      error_rate_rise?: number;
    },
    fetch?: typeof globalThis.fetch
  ) =>
    request<RegressionResult>(
      `/api/workspaces/${workspaceId}/datasets/${datasetId}/regression-check`,
      { method: 'POST', json: body, fetch }
    )
};

import { qs, request } from '$lib/api/http';
import type {
  AnalysisBundle,
  AnalysisIngestSummary,
  AnalysisResult,
  CostMetrics,
  FailureClusters,
  FailureMode,
  FailureModeMetrics,
  LatencyMetrics,
  PassRateMetrics,
  TokenMetrics,
  ToolMetrics
} from '$lib/api/types';

export const metricsApi = {
  // Each takes an optional `from`/`to` (ISO 8601) window + per-metric filter.

  metricsPassRate: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; grader?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<PassRateMetrics>(`/api/workspaces/${workspaceId}/metrics/pass-rate${qs(opts)}`, {
      fetch
    }),

  metricsFailureModes: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; grader?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<FailureModeMetrics>(`/api/workspaces/${workspaceId}/metrics/failure-modes${qs(opts)}`, {
      fetch
    }),

  metricsTools: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; tool_name?: string } = {},
    fetch?: typeof globalThis.fetch
  ) => request<ToolMetrics>(`/api/workspaces/${workspaceId}/metrics/tools${qs(opts)}`, { fetch }),

  metricsCost: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; model?: string } = {},
    fetch?: typeof globalThis.fetch
  ) => request<CostMetrics>(`/api/workspaces/${workspaceId}/metrics/cost${qs(opts)}`, { fetch }),

  metricsTokens: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; model?: string } = {},
    fetch?: typeof globalThis.fetch
  ) => request<TokenMetrics>(`/api/workspaces/${workspaceId}/metrics/tokens${qs(opts)}`, { fetch }),

  metricsLatency: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<LatencyMetrics>(`/api/workspaces/${workspaceId}/metrics/latency${qs(opts)}`, {
      fetch
    }),

  /**
   * Failing traces grouped by failure mode (§J.6). A first-order view (not under
   * `/metrics`) — each cluster carries example `run_id`s that link straight into
   * the trace viewer. v1 clusters by the stable taxonomy slug.
   */
  clusters: (
    workspaceId: string,
    opts: {
      from?: string;
      to?: string;
      experiment_id?: string;
      grader?: string;
      limit?: number;
    } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<FailureClusters>(`/api/workspaces/${workspaceId}/clusters${qs(opts)}`, {
      fetch
    }),

  // --- Failure-mode taxonomy (2A) --------------------------------------

  listFailureModes: (
    workspaceId: string,
    opts: { status?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<{ items: FailureMode[] }>(`/api/workspaces/${workspaceId}/failure-modes${qs(opts)}`, {
      fetch
    }),

  promoteFailureMode: (workspaceId: string, id: string, fetch?: typeof globalThis.fetch) =>
    request<FailureMode>(`/api/workspaces/${workspaceId}/failure-modes/${id}/promote`, {
      method: 'POST',
      fetch
    }),

  retireFailureMode: (workspaceId: string, id: string, fetch?: typeof globalThis.fetch) =>
    request<FailureMode>(`/api/workspaces/${workspaceId}/failure-modes/${id}/retire`, {
      method: 'POST',
      fetch
    }),

  mergeFailureMode: (
    workspaceId: string,
    id: string,
    intoId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<FailureMode>(`/api/workspaces/${workspaceId}/failure-modes/${id}/merge`, {
      method: 'POST',
      json: { into_id: intoId },
      fetch
    }),

  editFailureMode: (
    workspaceId: string,
    id: string,
    patch: { title?: string; definition?: string },
    fetch?: typeof globalThis.fetch
  ) =>
    request<FailureMode>(`/api/workspaces/${workspaceId}/failure-modes/${id}`, {
      method: 'PATCH',
      json: patch,
      fetch
    }),

  // --- Error-analysis bundle / ingest (2C) -----------------------------

  analysisBundle: (
    workspaceId: string,
    experimentId: string,
    opts: { iteration?: number; all?: boolean } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<AnalysisBundle>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/analysis/bundle${qs({
        iteration: opts.iteration,
        all: opts.all ? 'true' : undefined
      })}`,
      { fetch }
    ),

  analysisIngest: (
    workspaceId: string,
    experimentId: string,
    result: AnalysisResult,
    fetch?: typeof globalThis.fetch
  ) =>
    request<AnalysisIngestSummary>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/analysis/ingest`,
      { method: 'POST', json: result, fetch }
    )
};

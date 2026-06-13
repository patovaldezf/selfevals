/**
 * Thin typed wrapper around the FastAPI bridge.
 *
 * We don't generate types from OpenAPI yet. The shapes here are
 * mirrors of `selfevals.api.schemas` — keep them in sync by hand
 * until the cost outweighs the friction.
 */

const DEFAULT_BASE = '';

export type WorkspaceSummary = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  owner_id: string | null;
  created_at: string;
  experiment_count: number;
  last_run_at: string | null;
};

export type WorkspaceDetail = WorkspaceSummary & {
  experiment_count: number;
  recent_health: number | null;
};

export type ExperimentSummary = {
  id: string;
  name: string;
  goal: string;
  mode: string;
  state: string;
  primary_metric: string;
  primary_target: { operator: string; value: number };
  proposer_strategy: string;
  max_iterations: number;
  created_at: string;
  updated_at: string;
  iteration_count: number;
};

export type IterationSummary = {
  id: string;
  iteration: number;
  state: string;
  hypothesis: string;
  proposed_parameters: Record<string, unknown>;
  primary_metric_name: string | null;
  primary_metric_value: number | null;
  delta_vs_best: number | null;
  decision_outcome: string | null;
  decision_rationale: string | null;
  cost_usd: number | null;
  duration_seconds: number | null;
  trace_run_ids: string[];
  created_at: string;
};

export type ExperimentDetail = {
  summary: ExperimentSummary;
  result: Record<string, unknown> | null;
  iterations: IterationSummary[];
};

export type ExperimentListPage = {
  items: ExperimentSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type DecisionRow = {
  id: string;
  iteration: number;
  outcome: string;
  automated_rationale: string;
  human_rationale: string | null;
  metrics_snapshot: Record<string, number>;
  created_at: string;
};

export type AnchorPoint = {
  experiment_id: string;
  experiment_name: string;
  iteration: number;
  primary_metric_name: string;
  primary_metric_value: number;
  decision_outcome: string;
  created_at: string;
};

export type SpanSummary = {
  id: string;
  parent_id: string | null;
  kind: string;
  name: string;
  started_at: string;
  duration_ms: number;
  detail: Record<string, unknown>;
};

export type TraceDetail = {
  id: string;
  run_id: string;
  experiment_id: string | null;
  experiment_name: string | null;
  iteration: number | null;
  thread_id: string | null;
  thread_position: number | null;
  final_state: string;
  started_at: string;
  ended_at: string | null;
  spans: SpanSummary[];
  metrics: Record<string, unknown>;
};

export type ThreadTurn = {
  trace_id: string;
  run_id: string;
  position: number;
  experiment_id: string | null;
  iteration: number | null;
  final_state: string;
  started_at: string;
  ended_at: string | null;
  primary_grade: string | null;
  grader_results: Record<string, unknown>[];
  metrics: Record<string, unknown>;
};

export type ThreadDetail = {
  thread_id: string;
  turn_count: number;
  turns: ThreadTurn[];
};

/**
 * One rolled-up funnel node (B2). Recursive: `children` is keyed by the
 * child node's `key`. Mirror of `selfevals.api.schemas.FunnelNodeResponse`.
 * The backend owns all the rollup math — the FE only renders this tree.
 */
export type FunnelNode = {
  key: string;
  count: number;
  mean_score: number | null;
  total_weight: number;
  label_counts: Record<string, number>;
  failure_mode_counts: Record<string, number>;
  children: Record<string, FunnelNode>;
};

export type FunnelDetail = {
  iteration_id: string;
  iteration: number;
  // Empty when no grader emitted a structured breakdown (the common case).
  nodes: Record<string, FunnelNode>;
};

// --- Compare (B3) -------------------------------------------------------
// Mirrors `selfevals.api.schemas.CompareResponse`. The server is the single
// source of truth for the diff math (the reporter's `compute_compare`); the
// FE renders this directly instead of recomputing deltas client-side.

export type CompareParamRow = {
  key: string;
  a: string;
  b: string;
  changed: boolean;
};

export type CompareMetricRow = {
  name: string;
  a: number | null;
  b: number | null;
  delta: number | null;
};

export type CompareFunnelRow = {
  path: string;
  a: number | null;
  b: number | null;
  delta: number | null;
};

export type CompareFailureModes = {
  only_a: Record<string, number>;
  only_b: Record<string, number>;
  common: Record<string, [number, number]>;
};

export type CompareRecommendation = {
  kind: 'winner' | 'tie' | 'different_metric' | 'none';
  winner: string | null;
  metric_name: string | null;
  a_metric_name: string | null;
  b_metric_name: string | null;
  a_value: number | null;
  b_value: number | null;
  delta: number | null;
  new_failure_modes: string[];
};

export type CompareResponse = {
  a_id: string;
  b_id: string;
  a_iteration: number;
  b_iteration: number;
  a_created_at: string;
  b_created_at: string;
  a_decision: string | null;
  b_decision: string | null;
  proposal_diff: CompareParamRow[];
  metrics_diff: CompareMetricRow[];
  failure_modes: CompareFailureModes;
  funnel_diff: CompareFunnelRow[];
  recommendation: CompareRecommendation;
  holdout_status: string;
};

// --- Metrics (observability layer) -------------------------------------
// Mirror of `selfevals.api.schemas.*MetricsResponse`. Every metrics endpoint
// shares the `{ workspace_id, window, experiment_id, total, items }` envelope;
// only the row shape differs. All take a `from`/`to` time-range (ISO strings).

export type MetricsWindow = { start: string | null; end: string | null };

type MetricsEnvelope<Row> = {
  workspace_id: string;
  window: MetricsWindow;
  experiment_id: string | null;
  total: number;
  items: Row[];
};

export type PassRateRow = { grader: string; label: string; count: number; rate: number };
export type FailureModeRow = { failure_mode: string; count: number; rate: number };
export type ToolRow = {
  tool_name: string;
  status: string;
  count: number;
  error_count: number;
  avg_duration_ms: number | null;
  retry_count: number;
};
export type CostRow = {
  provider: string;
  model: string;
  call_count: number;
  total_cost_usd: number;
  avg_cost_usd: number;
};
export type TokenRow = {
  provider: string;
  model: string;
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
};
export type LatencyRow = {
  metric: string;
  count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
};

export type PassRateMetrics = MetricsEnvelope<PassRateRow>;
export type FailureModeMetrics = MetricsEnvelope<FailureModeRow>;
export type ToolMetrics = MetricsEnvelope<ToolRow>;
export type CostMetrics = MetricsEnvelope<CostRow>;
export type TokenMetrics = MetricsEnvelope<TokenRow>;
export type LatencyMetrics = MetricsEnvelope<LatencyRow>;

// --- Datasets ----------------------------------------------------------

export type SplitAllocationView = {
  optimization: number;
  holdout: number;
  reliability: number;
  other: Record<string, number>;
};

export type DatasetStatisticsView = {
  total_cases: number;
  by_level: Record<string, number>;
  by_feature: Record<string, number>;
  by_source: Record<string, number>;
  by_risk: Record<string, number>;
  holdout_count: number;
  pii_breakdown: Record<string, number>;
};

export type DatasetSummary = {
  id: string;
  name: string;
  description: string | null;
  dataset_type: string;
  status: string;
  case_count: number;
  manifest_hash: string | null;
  created_at: string;
  updated_at: string;
};

export type DatasetListPage = {
  items: DatasetSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type FeatureRef = { id: string; name: string } | Record<string, unknown>;

export type CaseSummary = {
  id: string;
  name: string;
  task_type: string;
  modalities: string[];
  input: Record<string, unknown>;
  graders: string[];
  holdout: boolean;
  is_conversation: boolean;
  feature: FeatureRef | null;
  level: string | null;
  dataset_type: string | null;
  latest_run_id: string | null;
  latest_trace_id: string | null;
};

export type DatasetDetail = {
  id: string;
  name: string;
  description: string | null;
  dataset_type: string;
  status: string;
  case_count: number;
  manifest_hash: string | null;
  split_allocation: SplitAllocationView;
  statistics: DatasetStatisticsView | null;
  cases: CaseSummary[];
  created_at: string;
  updated_at: string;
};

// --- Run / workspace mutation contracts --------------------------------

export type RunExperimentResponse = {
  experiment_id: string;
  workspace_id: string;
  state: string;
  run_id: string | null;
  job_id: string | null;
  dispatch: string;
};

export type RunExperimentRequest = {
  spec_path?: string;
  spec_inline?: Record<string, unknown>;
  dataset_id?: string;
  max_iterations?: number;
  reps?: number;
  persist_traces?: 'none' | 'all' | 'failed';
};

export type CreateWorkspaceRequest = {
  slug: string;
  name?: string;
  description?: string;
};

export type CreateDatasetRequest = {
  name: string;
  description?: string;
  dataset_type?: string;
  cases?: Record<string, unknown>[];
  cases_path?: string;
  split_allocation?: Record<string, number>;
};

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.body = body;
  }

  /** A human-facing message: FastAPI's `{detail}` if present, else the status. */
  get detail(): string {
    const b = this.body;
    if (b && typeof b === 'object' && 'detail' in b) {
      const d = (b as { detail: unknown }).detail;
      if (typeof d === 'string') return d;
    }
    if (typeof b === 'string' && b.trim()) return b;
    return `Request failed (${this.status})`;
  }
}

type RequestInitX = Omit<RequestInit, 'body'> & {
  fetch?: typeof fetch;
  /** JSON body — serialized and sent with `Content-Type: application/json`. */
  json?: unknown;
  /** Multipart body — sent as-is; the browser owns the `Content-Type` boundary. */
  form?: FormData;
};

async function request<T>(path: string, init?: RequestInitX): Promise<T> {
  const f = init?.fetch ?? fetch;
  const { fetch: _f, json, form, headers, ...rest } = init ?? {};

  // A bare GET keeps the original `X-SelfEvals-User` + JSON content type. A
  // mutation either serializes `json` (JSON content type) or passes `form`
  // through *without* setting Content-Type so the browser appends the
  // multipart boundary itself — setting it by hand corrupts the upload.
  const mergedHeaders: Record<string, string> = {
    'X-SelfEvals-User': 'local',
    ...(form ? {} : { 'Content-Type': 'application/json' }),
    ...((headers as Record<string, string>) ?? {})
  };

  const res = await f(DEFAULT_BASE + path, {
    ...rest,
    headers: mergedHeaders,
    body: form ?? (json !== undefined ? JSON.stringify(json) : undefined)
  });
  if (!res.ok) {
    // Read the body once as text, then try JSON. Calling `res.json()` then
    // `res.text()` in a fallback consumes the body on the first call and
    // the second throws `TypeError: Body is unusable: Body has already
    // been read` — which masks the real upstream error with a confusing
    // one. Text-first lets us preserve the original body either way.
    const raw = await res.text();
    let body: unknown = raw;
    try {
      body = JSON.parse(raw);
    } catch {
      // not JSON; keep raw text
    }
    throw new ApiError(res.status, body);
  }
  // 204 / empty body (some mutations ack without content).
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Build a `?from=&to=&...` query string, dropping undefined values. */
function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export const api = {
  health: (fetch?: typeof globalThis.fetch) =>
    request<{ status: string; db_path: string }>('/api/health', { fetch }),

  listWorkspaces: (fetch?: typeof globalThis.fetch) =>
    request<{ workspaces: WorkspaceSummary[] }>('/api/workspaces', { fetch }),

  workspace: (id: string, fetch?: typeof globalThis.fetch) =>
    request<WorkspaceDetail>(`/api/workspaces/${id}`, { fetch }),

  /** Create a workspace. Returns the new workspace; redirect to its slug. */
  createWorkspace: (body: CreateWorkspaceRequest, fetch?: typeof globalThis.fetch) =>
    request<WorkspaceDetail>('/api/workspaces', { method: 'POST', json: body, fetch }),

  listExperiments: (
    workspaceId: string,
    fetch?: typeof globalThis.fetch,
    options: { limit?: number; offset?: number } = {}
  ) =>
    // A8: server returns a paginated envelope. Default page size matches
    // the server default (100) so the FE doesn't have to track it.
    request<ExperimentListPage>(
      `/api/workspaces/${workspaceId}/experiments${qs({
        limit: options.limit,
        offset: options.offset
      })}`,
      { fetch }
    ),

  /**
   * Launch a run. The body carries exactly one of `spec_path` / `spec_inline`
   * (the form enforces this). Returns 202 with `dispatch` — `redis-worker`
   * needs a live `selfevals worker runs`; the caller surfaces that.
   */
  runExperiment: (
    workspaceId: string,
    body: RunExperimentRequest,
    fetch?: typeof globalThis.fetch
  ) =>
    request<RunExperimentResponse>(`/api/workspaces/${workspaceId}/experiments/run`, {
      method: 'POST',
      json: body,
      fetch
    }),

  cancelExperiment: (
    workspaceId: string,
    experimentId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<RunExperimentResponse>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/cancel`,
      { method: 'POST', fetch }
    ),

  experiment: (workspaceId: string, experimentId: string, fetch?: typeof globalThis.fetch) =>
    request<ExperimentDetail>(`/api/workspaces/${workspaceId}/experiments/${experimentId}`, {
      fetch
    }),

  decisions: (workspaceId: string, experimentId: string, fetch?: typeof globalThis.fetch) =>
    request<DecisionRow[]>(`/api/workspaces/${workspaceId}/experiments/${experimentId}/decisions`, {
      fetch
    }),

  trace: (workspaceId: string, traceId: string, fetch?: typeof globalThis.fetch) =>
    request<TraceDetail>(`/api/workspaces/${workspaceId}/traces/${traceId}`, {
      fetch
    }),

  anchorSet: (workspaceId: string, fetch?: typeof globalThis.fetch) =>
    request<AnchorPoint[]>(`/api/workspaces/${workspaceId}/anchor-set`, {
      fetch
    }),

  /**
   * Resolve a `*_pointer` field from a span detail. The API returns the
   * raw bytes; the caller decides how to render (JSON, markdown, plain).
   * Throws `ApiError` (400/404/500) on failure — the trace viewer
   * surfaces those inline so a missing payload doesn't break the page.
   */
  resolvePayload: async (
    workspaceId: string,
    pointer: string,
    fetch?: typeof globalThis.fetch
  ): Promise<{ text: string; isJson: boolean }> => {
    const f = fetch ?? globalThis.fetch;
    const url = `/api/workspaces/${workspaceId}/payloads?pointer=${encodeURIComponent(pointer)}`;
    const res = await f(url, {
      headers: {
        'X-SelfEvals-User': 'local'
      }
    });
    if (!res.ok) {
      // Reuse the same body-reading discipline as request() — read once
      // as text, then try to parse. See client.ts request() for the
      // history of the "Body has already been read" bug.
      const raw = await res.text();
      let body: unknown = raw;
      try {
        body = JSON.parse(raw);
      } catch {
        // not JSON
      }
      throw new ApiError(res.status, body);
    }
    const text = await res.text();
    const isJson = res.headers.get('content-type')?.startsWith('application/json') ?? false;
    return { text, isJson };
  },

  thread: (workspaceId: string, threadId: string, fetch?: typeof globalThis.fetch) =>
    request<ThreadDetail>(`/api/workspaces/${workspaceId}/threads/${threadId}`, {
      fetch
    }),

  /**
   * Per-iteration grader funnel drill-down (B2). Lazy-loaded only when the
   * user opens the Funnel tab — the funnel is additive/informational, so it
   * stays off the experiment page's server load. `nodes` is empty when no
   * grader emitted a structured breakdown. Throws `ApiError` (404) for an
   * unknown iteration.
   */
  iterationFunnel: (workspaceId: string, iterationId: string, fetch?: typeof globalThis.fetch) =>
    request<FunnelDetail>(`/api/workspaces/${workspaceId}/iterations/${iterationId}/funnel`, {
      fetch
    }),

  /**
   * Server-rendered structured diff of two iterations (B3). The diff math
   * (metric deltas, recommendation, failure-mode set arithmetic) lives in
   * the backend reporter — the FE only renders the result.
   */
  compare: (
    workspaceId: string,
    experimentId: string,
    a: string,
    b: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<CompareResponse>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
      { fetch }
    ),

  // --- Datasets --------------------------------------------------------

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
  uploadDataset: (
    workspaceId: string,
    form: FormData,
    fetch?: typeof globalThis.fetch
  ) =>
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

  // --- Metrics (observability) -----------------------------------------
  // Each takes an optional `from`/`to` (ISO 8601) window + per-metric filter.

  metricsPassRate: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; grader?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<PassRateMetrics>(
      `/api/workspaces/${workspaceId}/metrics/pass-rate${qs(opts)}`,
      { fetch }
    ),

  metricsFailureModes: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; grader?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<FailureModeMetrics>(
      `/api/workspaces/${workspaceId}/metrics/failure-modes${qs(opts)}`,
      { fetch }
    ),

  metricsTools: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; tool_name?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<ToolMetrics>(`/api/workspaces/${workspaceId}/metrics/tools${qs(opts)}`, { fetch }),

  metricsCost: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; model?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<CostMetrics>(`/api/workspaces/${workspaceId}/metrics/cost${qs(opts)}`, { fetch }),

  metricsTokens: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string; model?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<TokenMetrics>(`/api/workspaces/${workspaceId}/metrics/tokens${qs(opts)}`, { fetch }),

  metricsLatency: (
    workspaceId: string,
    opts: { from?: string; to?: string; experiment_id?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<LatencyMetrics>(`/api/workspaces/${workspaceId}/metrics/latency${qs(opts)}`, {
      fetch
    })
};

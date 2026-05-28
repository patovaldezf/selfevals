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
  created_at: string;
};

export type ExperimentDetail = {
  summary: ExperimentSummary;
  result: Record<string, unknown> | null;
  iterations: IterationSummary[];
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
  iteration: number | null;
  final_state: string;
  started_at: string;
  ended_at: string | null;
  spans: SpanSummary[];
  metrics: Record<string, unknown>;
};

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { fetch?: typeof fetch }
): Promise<T> {
  const f = init?.fetch ?? fetch;
  const res = await f(DEFAULT_BASE + path, {
    ...init,
    headers: {
      'X-SelfEvals-User': 'local',
      'Content-Type': 'application/json',
      ...(init?.headers ?? {})
    }
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
  return res.json() as Promise<T>;
}

export const api = {
  health: (fetch?: typeof globalThis.fetch) =>
    request<{ status: string; db_path: string }>('/api/health', { fetch }),

  listWorkspaces: (fetch?: typeof globalThis.fetch) =>
    request<{ workspaces: WorkspaceSummary[] }>('/api/workspaces', { fetch }),

  workspace: (id: string, fetch?: typeof globalThis.fetch) =>
    request<WorkspaceDetail>(`/api/workspaces/${id}`, { fetch }),

  listExperiments: (workspaceId: string, fetch?: typeof globalThis.fetch) =>
    request<ExperimentSummary[]>(
      `/api/workspaces/${workspaceId}/experiments`,
      { fetch }
    ),

  experiment: (
    workspaceId: string,
    experimentId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<ExperimentDetail>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}`,
      { fetch }
    ),

  decisions: (
    workspaceId: string,
    experimentId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<DecisionRow[]>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/decisions`,
      { fetch }
    ),

  trace: (
    workspaceId: string,
    traceId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<TraceDetail>(`/api/workspaces/${workspaceId}/traces/${traceId}`, {
      fetch
    }),

  anchorSet: (workspaceId: string, fetch?: typeof globalThis.fetch) =>
    request<AnchorPoint[]>(`/api/workspaces/${workspaceId}/anchor-set`, {
      fetch
    })
};

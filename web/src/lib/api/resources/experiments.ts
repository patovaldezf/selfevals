import { qs, request } from '$lib/api/http';
import type {
  AnchorPoint,
  CompareResponse,
  DecisionRow,
  ExperimentDetail,
  ExperimentListPage,
  ExperimentResults,
  FunnelDetail,
  RunExperimentRequest,
  RunExperimentResponse
} from '$lib/api/types';

export const experimentsApi = {
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

  cancelExperiment: (workspaceId: string, experimentId: string, fetch?: typeof globalThis.fetch) =>
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

  /**
   * Per-scenario expected-vs-detected-vs-matched for the experiment's best
   * iteration. Lazy-loaded only when the Results tab opens — it can be large.
   * `includeTurns` expands each conversation case into per-turn `ScenarioResult`s
   * (off by default). Cases with no persisted trace are still listed
   * (`detected`/`matched` null) so the grid is honest.
   */
  experimentResults: (
    workspaceId: string,
    experimentId: string,
    opts: { includeTurns?: boolean } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<ExperimentResults>(
      `/api/workspaces/${workspaceId}/experiments/${experimentId}/results${qs({
        include: opts.includeTurns ? 'turns' : undefined
      })}`,
      { fetch }
    ),

  anchorSet: (workspaceId: string, fetch?: typeof globalThis.fetch) =>
    request<AnchorPoint[]>(`/api/workspaces/${workspaceId}/anchor-set`, {
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
    )
};

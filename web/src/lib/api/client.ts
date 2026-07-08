/**
 * Typed wrapper around the FastAPI bridge — barrel re-export.
 *
 * Split by resource under `api/resources/*`, all sharing the single
 * request/auth/error layer in `api/http.ts`. Kept as one `api` object
 * (same method names as before the split) so existing call sites don't
 * change; `types.ts` holds the hand-maintained response/request shapes,
 * cross-checked against the generated `types.gen.ts` (`npm run gen:api`).
 */

export { ApiError } from '$lib/api/http';
export * from '$lib/api/types';

import { arenaApi } from '$lib/api/resources/arena';
import { datasetsApi } from '$lib/api/resources/datasets';
import { experimentsApi } from '$lib/api/resources/experiments';
import { metricsApi } from '$lib/api/resources/metrics';
import { pairwiseApi } from '$lib/api/resources/pairwise';
import { tracesApi } from '$lib/api/resources/traces';
import { workspacesApi } from '$lib/api/resources/workspaces';

export const api = {
  ...workspacesApi,
  ...experimentsApi,
  ...tracesApi,
  ...datasetsApi,
  ...metricsApi,
  ...pairwiseApi,
  ...arenaApi
};

import { request } from '$lib/api/http';
import type { CreateWorkspaceRequest, WorkspaceDetail, WorkspaceSummary } from '$lib/api/types';

export const workspacesApi = {
  health: (fetch?: typeof globalThis.fetch) =>
    request<{
      status: string;
      db_path: string;
      storage_url?: string | null;
      storage_backend?: string;
    }>('/api/health', { fetch }),

  listWorkspaces: (fetch?: typeof globalThis.fetch) =>
    request<{ workspaces: WorkspaceSummary[] }>('/api/workspaces', { fetch }),

  workspace: (id: string, fetch?: typeof globalThis.fetch) =>
    request<WorkspaceDetail>(`/api/workspaces/${id}`, { fetch }),

  /** Create a workspace. Returns the new workspace; redirect to its slug. */
  createWorkspace: (body: CreateWorkspaceRequest, fetch?: typeof globalThis.fetch) =>
    request<WorkspaceDetail>('/api/workspaces', { method: 'POST', json: body, fetch }),

  /** Runs currently streaming spans through the broker, across all workspaces.
   *  The caller filters by workspace. Used to surface a live run and open its
   *  trace stream. (The backend's `ActiveRun` carries no experiment_id.) */
  activeRuns: (fetch?: typeof globalThis.fetch) =>
    request<{ runs: { workspace_id: string; run_id: string }[] }>('/api/runs/active', { fetch })
};

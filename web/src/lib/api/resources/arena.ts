import { qs, request } from '$lib/api/http';
import type {
  ArenaBundle,
  ArenaRound,
  ArenaSummary,
  ArenaVariant,
  CreateArenaRequest,
  GitRefsResponse,
  LaunchRoundRequest,
  PromoteVariantResponse,
  RegisterVariantRequest
} from '$lib/api/types';

export const arenaApi = {
  listArenas: (workspaceId: string, fetch?: typeof globalThis.fetch) =>
    request<ArenaSummary[]>(`/api/workspaces/${workspaceId}/arenas`, { fetch }),

  arena: (workspaceId: string, arenaId: string, fetch?: typeof globalThis.fetch) =>
    request<ArenaSummary>(`/api/workspaces/${workspaceId}/arenas/${arenaId}`, { fetch }),

  createArena: (workspaceId: string, body: CreateArenaRequest, fetch?: typeof globalThis.fetch) =>
    request<ArenaSummary>(`/api/workspaces/${workspaceId}/arenas`, {
      method: 'POST',
      json: body,
      fetch
    }),

  deleteArena: (workspaceId: string, arenaId: string, fetch?: typeof globalThis.fetch) =>
    request<void>(`/api/workspaces/${workspaceId}/arenas/${arenaId}`, {
      method: 'DELETE',
      fetch
    }),

  listArenaVariants: (workspaceId: string, arenaId: string, fetch?: typeof globalThis.fetch) =>
    request<ArenaVariant[]>(`/api/workspaces/${workspaceId}/arenas/${arenaId}/variants`, { fetch }),

  /** Register a variant against an existing git ref. Returns 202 — the
   *  worktree/setup_command prepare in the background; poll listArenaVariants
   *  until state flips to "ready" or "failed". */
  registerArenaVariant: (
    workspaceId: string,
    arenaId: string,
    body: RegisterVariantRequest,
    fetch?: typeof globalThis.fetch
  ) =>
    request<ArenaVariant>(`/api/workspaces/${workspaceId}/arenas/${arenaId}/variants`, {
      method: 'POST',
      json: body,
      fetch
    }),

  listArenaRounds: (workspaceId: string, arenaId: string, fetch?: typeof globalThis.fetch) =>
    request<ArenaRound[]>(`/api/workspaces/${workspaceId}/arenas/${arenaId}/rounds`, { fetch }),

  arenaRound: (
    workspaceId: string,
    arenaId: string,
    roundId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<ArenaRound>(`/api/workspaces/${workspaceId}/arenas/${arenaId}/rounds/${roundId}`, {
      fetch
    }),

  /** Launch a round: every ready variant (or the given subset) runs in
   *  parallel as its own child experiment. Returns 202. */
  launchArenaRound: (
    workspaceId: string,
    arenaId: string,
    body: LaunchRoundRequest,
    fetch?: typeof globalThis.fetch
  ) =>
    request<ArenaRound>(`/api/workspaces/${workspaceId}/arenas/${arenaId}/rounds`, {
      method: 'POST',
      json: body,
      fetch
    }),

  /** Cross-variant view: leaderboard, pairwise diffs against the best,
   *  exemplar failures. Defaults to the most recently launched round. */
  arenaBundle: (
    workspaceId: string,
    arenaId: string,
    round?: number,
    fetch?: typeof globalThis.fetch
  ) =>
    request<ArenaBundle>(
      `/api/workspaces/${workspaceId}/arenas/${arenaId}/bundle${qs({ round })}`,
      { fetch }
    ),

  /** Mark a variant as the winner. Never touches git — returns suggested
   *  merge/PR commands for a human to run. */
  promoteArenaVariant: (
    workspaceId: string,
    arenaId: string,
    variantId: string,
    fetch?: typeof globalThis.fetch
  ) =>
    request<PromoteVariantResponse>(`/api/workspaces/${workspaceId}/arenas/${arenaId}/promote`, {
      method: 'POST',
      json: { variant_id: variantId },
      fetch
    }),

  /** List local branches of a git repo on the server, for the variant picker. */
  gitRefs: (workspaceId: string, repoPath: string, fetch?: typeof globalThis.fetch) =>
    request<GitRefsResponse>(
      `/api/workspaces/${workspaceId}/git/refs${qs({ repo_path: repoPath })}`,
      { fetch }
    )
};

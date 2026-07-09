import { api } from '$lib/api/client';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch, params }) => {
  try {
    const [arena, variants, rounds, bundle] = await Promise.all([
      api.arena(params.workspace, params.arena, fetch),
      api.listArenaVariants(params.workspace, params.arena, fetch),
      api.listArenaRounds(params.workspace, params.arena, fetch),
      // The bundle is what drives the Leaderboard/Matrix tabs; a fresh arena
      // with no completed round yet still returns one (empty leaderboard).
      api.arenaBundle(params.workspace, params.arena, undefined, fetch).catch(() => null)
    ]);
    return { arena, variants, rounds, bundle };
  } catch (err) {
    if (
      typeof err === 'object' &&
      err !== null &&
      'status' in err &&
      (err as { status: number }).status === 404
    ) {
      throw error(404, 'Arena not found');
    }
    throw err;
  }
};

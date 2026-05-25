import { api } from '$lib/api/client';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
  try {
    const data = await api.listWorkspaces(fetch);
    return { workspaces: data.workspaces, error: null as string | null };
  } catch (err) {
    return {
      workspaces: [],
      error: err instanceof Error ? err.message : 'API unreachable'
    };
  }
};

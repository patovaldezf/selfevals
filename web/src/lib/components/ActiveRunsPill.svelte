<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { api } from '$lib/api/client';

  type ActiveRun = { workspace_id: string; run_id: string };

  let runs: ActiveRun[] = [];
  let timer: ReturnType<typeof setInterval> | null = null;
  let loading = false;

  async function fetchActive() {
    if (loading) return;
    loading = true;
    try {
      const body = await api.activeRuns();
      runs = body.runs ?? [];
    } catch {
      /* keep the last known state; failures here aren't user-facing */
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    fetchActive();
    timer = setInterval(fetchActive, 2000);
  });

  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  export let workspaceId: string | null = null;
  $: visible = workspaceId ? runs.filter((r) => r.workspace_id === workspaceId) : runs;
</script>

{#if visible.length > 0}
  <div class="flex flex-col gap-1">
    <div class="text-xs uppercase tracking-wide text-text-3">Live</div>
    {#each visible as r}
      <a
        href={`/${r.workspace_id}/traces/${r.run_id}`}
        class="group flex items-center gap-2 rounded-md px-3 py-1.5 text-xs hover:bg-surface-2 transition-colors"
      >
        <span
          class="inline-block h-1.5 w-1.5 rounded-full bg-success animate-pulse"
          aria-hidden="true"
        ></span>
        <span class="font-mono text-text-2 truncate flex-1">{r.run_id}</span>
        <span class="text-text-3 group-hover:text-text-1" aria-hidden="true">→</span>
      </a>
    {/each}
  </div>
{/if}

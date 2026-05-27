<script lang="ts">
  import AppShell from '$lib/components/AppShell.svelte';
  import type { PageData } from './$types';

  export let data: PageData;

  function relativeTime(iso: string | null): string {
    if (!iso) return '—';
    const then = new Date(iso).getTime();
    const diff = Date.now() - then;
    const mins = Math.floor(diff / 60_000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  }
</script>

<svelte:head>
  <title>selfevals</title>
</svelte:head>

<AppShell>
  <div class="max-w-5xl mx-auto px-12 py-16">
    <header class="mb-12">
      <h1 class="text-3xl font-semibold tracking-tight">Workspaces</h1>
      <p class="text-text-2 mt-2 text-sm">
        Every experiment belongs to a workspace. Pick one to dive in.
      </p>
    </header>

    {#if data.error}
      <div class="rounded-lg border border-border bg-surface px-6 py-8 text-sm text-text-2">
        <div class="font-medium text-text-1 mb-1">Backend unreachable</div>
        <div class="font-mono text-xs text-text-3 mb-3">{data.error}</div>
        <p>
          Start the API:
          <code class="font-mono text-xs px-1.5 py-0.5 rounded bg-surface-2"
            >uv run selfevals-api</code
          >.
        </p>
      </div>
    {:else if data.workspaces.length === 0}
      <div class="rounded-lg border border-border bg-surface px-6 py-16 text-center">
        <p class="text-text-2 mb-4">
          No workspaces yet. Seed one from the CLI:
        </p>
        <code class="font-mono text-xs px-2 py-1 rounded bg-surface-2"
          >uv run selfevals init my-team</code
        >
      </div>
    {:else}
      <ul class="divide-y divide-border border border-border rounded-lg overflow-hidden bg-surface">
        {#each data.workspaces as ws}
          <li>
            <a
              href={`/${ws.id}`}
              class="flex items-center justify-between px-6 py-5 hover:bg-surface-2 transition-colors"
            >
              <div class="min-w-0">
                <div class="flex items-baseline gap-2">
                  <span class="font-medium">{ws.name}</span>
                  <span class="font-mono text-xs text-text-3">{ws.slug}</span>
                </div>
                <div class="text-sm text-text-3 mt-1 truncate">
                  {ws.description ?? `${ws.experiment_count} experiments`}
                </div>
              </div>
              <div class="flex items-center gap-8 text-sm">
                <div class="text-right">
                  <div class="text-text-3 text-xs">last run</div>
                  <div class="font-mono text-text-2">{relativeTime(ws.last_run_at)}</div>
                </div>
                <div class="text-right">
                  <div class="text-text-3 text-xs">experiments</div>
                  <div class="font-mono text-text-1" data-numeric>{ws.experiment_count}</div>
                </div>
                <span class="text-text-3" aria-hidden="true">→</span>
              </div>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</AppShell>

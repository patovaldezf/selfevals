<script lang="ts">
  import type { LayoutData } from '../$types';
  export let data: LayoutData;
</script>

<svelte:head>
  <title>Clusters · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-5xl mx-auto">
  <header class="mb-8">
    <h1 class="text-2xl font-semibold tracking-tight">Failure clusters</h1>
    <p class="text-text-2 mt-1.5 text-sm">
      Group failing traces by failure mode, surface the top blockers, route them into Linear. Spec
      §J.6.
    </p>
  </header>

  <div class="grid grid-cols-2 gap-4">
    <div class="rounded-lg border border-border bg-surface px-5 py-5">
      <div class="text-xs uppercase tracking-wide text-text-3 mb-2">Top clusters</div>
      <ul class="space-y-3 text-sm text-text-3">
        <li class="flex justify-between">
          <span>missing_required_substring</span>
          <span class="font-mono" data-numeric>—</span>
        </li>
        <li class="flex justify-between">
          <span>tool_args_invalid</span>
          <span class="font-mono" data-numeric>—</span>
        </li>
        <li class="flex justify-between">
          <span>retrieval_miss</span>
          <span class="font-mono" data-numeric>—</span>
        </li>
      </ul>
    </div>
    <div class="rounded-lg border border-border bg-surface px-5 py-5">
      <div class="text-xs uppercase tracking-wide text-text-3 mb-2">Linear integration</div>
      <p class="text-sm text-text-2 leading-relaxed mb-4">
        Set <code class="font-mono text-xs px-1.5 py-0.5 rounded bg-surface-2">LINEAR_API_KEY</code> to
        file every regression cluster into Linear with one click.
      </p>
      <button
        type="button"
        class="text-xs font-medium px-3 py-1.5 rounded border border-border text-text-2 hover:bg-surface-2"
        disabled
      >
        Connect Linear (soon)
      </button>
    </div>
  </div>

  <div
    class="mt-12 rounded-lg border border-dashed border-border bg-surface px-8 py-12 text-center text-text-3 text-sm"
  >
    Cluster computation ships with the §J.6 module. This screen reserves real estate so the design
    doesn't bolt on later.
  </div>
</div>

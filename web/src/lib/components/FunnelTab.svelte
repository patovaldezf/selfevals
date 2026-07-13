<!--
  FunnelTab: pick an iteration and render its grader breakdown funnel.

  Self-contained and lazy, same molde as PairwisePanel/CompareTab: fetches
  on selection change, guarded by a request token against out-of-order
  responses. All rollup math lives in the backend; we only render.
-->
<script lang="ts">
  import FunnelNode from '$lib/components/FunnelNode.svelte';
  import FunnelNodeDrawer from '$lib/components/FunnelNodeDrawer.svelte';
  import {
    api,
    ApiError,
    type FunnelDetail,
    type FunnelNode as FunnelNodeType,
    type IterationSummary
  } from '$lib/api/client';

  export let workspaceId: string;
  export let iterations: IterationSummary[];
  export let best: IterationSummary | null;

  // Clicking a funnel node drills into its label + failure-mode breakdown.
  let selectedNode: FunnelNodeType | null = null;

  let funnelIterationId: string | null = null;
  let funnelDetail: FunnelDetail | null = null;
  let funnelError: string | null = null;
  let funnelLoading = false;
  // Token guards against an out-of-order response overwriting a newer one.
  let funnelRequest = 0;

  // Default the picker to the best iteration the first time the tab opens.
  $: if (funnelIterationId === null) {
    const fallback = best ?? iterations[0];
    if (fallback) funnelIterationId = fallback.id;
  }

  $: funnelIteration = iterations.find((it) => it.id === funnelIterationId) ?? null;

  // Fetch whenever the selected iteration changes.
  $: if (funnelIterationId !== null) {
    void loadFunnel(funnelIterationId);
  }

  async function loadFunnel(iterationId: string): Promise<void> {
    const token = ++funnelRequest;
    funnelLoading = true;
    funnelError = null;
    try {
      const detail = await api.iterationFunnel(workspaceId, iterationId);
      if (token !== funnelRequest) return; // a newer request superseded this one
      funnelDetail = detail;
    } catch (err) {
      if (token !== funnelRequest) return;
      funnelDetail = null;
      funnelError =
        err instanceof ApiError && err.status === 404
          ? 'Iteration not found.'
          : 'Could not load the funnel for this iteration.';
    } finally {
      if (token === funnelRequest) funnelLoading = false;
    }
  }

  $: funnelKeys = funnelDetail ? Object.keys(funnelDetail.nodes).sort() : [];
</script>

<div class="rounded-lg border border-border bg-surface">
  <div class="flex items-center justify-between gap-4 border-b border-border px-5 py-3.5">
    <div class="flex items-baseline gap-2">
      <span class="text-xs uppercase tracking-wide text-text-3">Funnel</span>
      {#if funnelIteration}
        <span class="font-mono text-xs text-text-3" data-numeric>
          iteration #{funnelIteration.iteration}
        </span>
      {/if}
    </div>
    <select
      class="font-mono text-xs px-2 py-1 rounded border border-border bg-bg"
      bind:value={funnelIterationId}
      aria-label="Select iteration"
    >
      {#each iterations as it}
        <option value={it.id}>#{it.iteration}</option>
      {/each}
    </select>
  </div>

  <div class="px-5 py-4">
    {#if funnelLoading}
      <div class="text-text-3 text-sm py-10 text-center">Loading funnel…</div>
    {:else if funnelError}
      <div class="text-danger text-sm py-10 text-center">{funnelError}</div>
    {:else if funnelDetail === null || funnelKeys.length === 0}
      <div class="py-10 text-center">
        <p class="text-text-2 text-sm">No grader breakdown recorded for this iteration.</p>
        <p class="text-text-3 text-xs mt-1.5 max-w-md mx-auto">
          The funnel appears when a grader emits a structured breakdown.
        </p>
      </div>
    {:else}
      <div
        class="mb-2 flex items-baseline justify-between text-[11px] uppercase tracking-wide text-text-3"
      >
        <span>Node · click to drill in</span>
        <span>Mean score</span>
      </div>
      <div class="divide-y divide-border/60">
        {#each funnelKeys as key (key)}
          <FunnelNode
            node={funnelDetail.nodes[key]}
            onSelect={(n) => (selectedNode = n)}
            selectedKey={selectedNode?.key ?? null}
          />
        {/each}
      </div>
    {/if}
  </div>
</div>

<FunnelNodeDrawer
  open={selectedNode !== null}
  node={selectedNode}
  onClose={() => (selectedNode = null)}
/>

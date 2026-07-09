<!--
  IterationDrawer: full detail for one iteration, in a slide-over.

  Presentational; the only state is which iteration is open, owned by the
  parent. Navigating to a trace closes the drawer via the `close` event so
  the underlying page state stays in sync with the URL change.
-->
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import BaselinePanel from '$lib/components/BaselinePanel.svelte';
  import { fmtNumber, fmtDelta } from '$lib/viz/format';
  import type { IterationSummary, DatasetSummary } from '$lib/api/client';

  export let iteration: IterationSummary;
  export let workspaceId: string;
  export let datasets: DatasetSummary[];

  const dispatch = createEventDispatcher<{ close: void }>();
</script>

<div
  class="fixed inset-0 bg-black/20 z-40"
  role="button"
  tabindex="0"
  on:click={() => dispatch('close')}
  on:keydown={(e) => e.key === 'Escape' && dispatch('close')}
></div>
<aside
  class="fixed top-0 right-0 h-full w-[480px] bg-surface border-l border-border z-50 overflow-y-auto px-6 py-7 shadow-2"
>
  <div class="flex items-baseline justify-between mb-6">
    <h2 class="text-lg font-semibold">Iteration #{iteration.iteration}</h2>
    <button
      type="button"
      on:click={() => dispatch('close')}
      class="text-text-3 hover:text-text-1 text-sm"
    >
      Close (Esc)
    </button>
  </div>

  <dl class="space-y-4 text-sm">
    <div>
      <dt class="text-text-3 text-xs mb-0.5">Hypothesis</dt>
      <dd>{iteration.hypothesis}</dd>
    </div>
    <div>
      <dt class="text-text-3 text-xs mb-0.5">Parameters</dt>
      <dd>
        <pre class="font-mono text-xs bg-surface-2 rounded p-3 overflow-x-auto">{JSON.stringify(
            iteration.proposed_parameters,
            null,
            2
          )}</pre>
      </dd>
    </div>
    <div class="grid grid-cols-2 gap-4">
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Primary metric</dt>
        <dd class="font-mono" data-numeric>{fmtNumber(iteration.primary_metric_value)}</dd>
      </div>
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Δ vs running best</dt>
        <dd class="font-mono" data-numeric>{fmtDelta(iteration.delta_vs_best)}</dd>
      </div>
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Cost (USD)</dt>
        <dd class="font-mono" data-numeric>{iteration.cost_usd ?? '—'}</dd>
      </div>
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Duration</dt>
        <dd class="font-mono" data-numeric>{iteration.duration_seconds ?? '—'}s</dd>
      </div>
    </div>
    <div>
      <dt class="text-text-3 text-xs mb-0.5">Decision</dt>
      <dd class="flex items-center gap-2">
        <DecisionBadge outcome={iteration.decision_outcome} />
        <span class="text-text-2">{iteration.decision_rationale ?? '—'}</span>
      </dd>
    </div>
    <div>
      <dt class="text-text-3 text-xs mb-1.5">
        Traces
        <span class="text-text-3 font-mono normal-case ml-1">
          · {iteration.trace_run_ids.length}
        </span>
      </dt>
      <dd>
        {#if iteration.trace_run_ids.length === 0}
          <span class="text-text-3 text-xs">No traces persisted for this iteration.</span>
        {:else}
          <ul class="space-y-1">
            {#each iteration.trace_run_ids as runId, idx}
              <li class="flex items-center gap-2">
                <a
                  href={`/${workspaceId}/traces/${runId}`}
                  on:click={() => dispatch('close')}
                  class="group flex flex-1 items-center justify-between gap-3 rounded border border-border bg-surface-2/40 hover:bg-surface-2 hover:border-text-3 px-2.5 py-1.5 transition-colors"
                >
                  <span class="text-sm text-text-2 group-hover:text-text-1">
                    Open trace #{idx + 1}
                  </span>
                  <span
                    class="text-text-3 group-hover:text-text-1 text-xs shrink-0"
                    aria-hidden="true"
                  >
                    →
                  </span>
                </a>
                <CopyableId id={runId} label="run id" />
              </li>
            {/each}
          </ul>
        {/if}
      </dd>
    </div>
    <div>
      <dt class="text-text-3 text-xs mb-0.5">Record id</dt>
      <dd><CopyableId id={iteration.id} label="iteration record id" /></dd>
    </div>
    {#if iteration.primary_metric_value !== null}
      <div class="pt-2 border-t border-border">
        <dt class="text-text-3 text-xs mb-2 uppercase tracking-wide">Baseline &amp; regression</dt>
        <dd>
          <BaselinePanel {workspaceId} iterationId={iteration.id} {datasets} />
        </dd>
      </div>
    {/if}
  </dl>
</aside>

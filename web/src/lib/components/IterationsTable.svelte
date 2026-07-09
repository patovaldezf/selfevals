<!--
  IterationsTable: the per-iteration ledger for an experiment.

  Purely presentational + one event: clicking (or Enter/Space on) a row asks
  the parent to open its drawer. No fetch, no state of its own.
-->
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import { fmtNumber, fmtDelta, deltaColor } from '$lib/viz/format';
  import { levelColor, thresholdLevel, type ThresholdDirection } from '$lib/viz/thresholds';
  import type { IterationSummary } from '$lib/api/client';

  export let iterations: IterationSummary[];
  export let best: IterationSummary | null;
  export let targetValue: number;
  export let targetDirection: ThresholdDirection;

  const dispatch = createEventDispatcher<{ select: IterationSummary }>();
</script>

<div class="border border-border rounded-lg overflow-hidden bg-surface">
  <table class="w-full text-sm">
    <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
      <tr>
        <th class="text-left px-4 py-2.5 w-12 font-medium">#</th>
        <th class="text-left px-4 py-2.5 font-medium">Parameters</th>
        <th class="text-right px-4 py-2.5 font-medium">Primary</th>
        <th class="text-right px-4 py-2.5 font-medium">Δ best</th>
        <th class="text-left px-4 py-2.5 font-medium">Decision</th>
        <th class="text-left px-4 py-2.5 font-medium">Rationale</th>
      </tr>
    </thead>
    <tbody class="divide-y divide-border">
      {#each iterations as it}
        {@const isBest = best !== null && it.id === best.id}
        <tr
          class="iter-row hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-1 transition-colors cursor-pointer"
          class:iter-best={isBest}
          role="button"
          tabindex="0"
          aria-label="Open details for iteration #{it.iteration}{isBest ? ' (best so far)' : ''}"
          on:click={() => dispatch('select', it)}
          on:keydown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              dispatch('select', it);
            }
          }}
        >
          <td class="px-4 py-3 font-mono text-text-3 text-xs" data-numeric>
            <span class="iter-num">
              {#if isBest}<span class="iter-best-mark" title="Best so far" aria-hidden="true"
                ></span>{/if}
              {it.iteration}
            </span>
          </td>
          <td class="px-4 py-3">
            {#if Object.keys(it.proposed_parameters).length === 0}
              <span class="text-text-3 text-xs">—</span>
            {:else}
              <div class="flex flex-wrap gap-1.5">
                {#each Object.entries(it.proposed_parameters) as [k, v]}
                  <span
                    class="font-mono text-[11px] px-1.5 py-0.5 rounded bg-surface-2 text-text-2"
                  >
                    {k}={typeof v === 'object' ? JSON.stringify(v) : String(v)}
                  </span>
                {/each}
              </div>
            {/if}
          </td>
          <td
            class="px-4 py-3 text-right font-mono"
            style:color={levelColor(
              thresholdLevel(it.primary_metric_value, {
                target: targetValue,
                direction: targetDirection
              })
            )}
            data-numeric
          >
            {fmtNumber(it.primary_metric_value)}
          </td>
          <td
            class="px-4 py-3 text-right font-mono text-xs"
            style:color={deltaColor(it.delta_vs_best, targetDirection)}
            data-numeric
          >
            {fmtDelta(it.delta_vs_best)}
          </td>
          <td class="px-4 py-3"><DecisionBadge outcome={it.decision_outcome} /></td>
          <td class="px-4 py-3 text-text-2 text-xs truncate max-w-md">
            {it.decision_rationale ?? '—'}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  /* The winning iteration gets a quiet left accent + dot so "which one is best"
     is obvious at a glance while a run climbs. */
  .iter-best {
    background: var(--color-ok-subtle);
    box-shadow: inset 2px 0 0 var(--color-ok);
  }
  .iter-best:hover {
    background: color-mix(in srgb, var(--color-ok) 14%, transparent);
  }
  .iter-num {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  .iter-best-mark {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--color-ok);
    flex-shrink: 0;
  }
</style>

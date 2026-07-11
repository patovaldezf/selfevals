<!--
  CompareTab: pick two iterations and render the server-computed diff.

  Self-contained and lazy — same molde as PairwisePanel: the parent renders
  it only when the tab is open, and a request token guards against an
  out-of-order response overwriting a newer selection. All delta math is
  server-side (one source shared with the CLI); this only renders it.
-->
<script lang="ts">
  import { api, ApiError, type CompareResponse, type IterationSummary } from '$lib/api/client';
  import { fmtNumber, fmtDelta, deltaColor } from '$lib/viz/format';
  import type { ThresholdDirection } from '$lib/viz/thresholds';
  import CompareVerdict from './CompareVerdict.svelte';
  import DeltaStat from './charts/DeltaStat.svelte';
  import Pill from './ui/Pill.svelte';

  // The compare endpoint reports a delta per metric but not its direction, so a
  // metric where lower is better (latency, cost, error/failure counts) would
  // colour a drop as "bad" under the primary metric's direction. Infer the
  // direction from the metric name so the colour is honest — up is not always
  // good. Falls back to the experiment's primary direction.
  const LOWER_IS_BETTER = /(latency|_ms|cost|usd|error|fail|duration)/i;
  function directionFor(name: string): ThresholdDirection {
    return LOWER_IS_BETTER.test(name) ? 'lower' : targetDirection;
  }

  export let workspaceId: string;
  export let experimentId: string;
  export let iterations: IterationSummary[];
  export let best: IterationSummary | null;
  export let targetDirection: ThresholdDirection;

  let compareA: string | null = null;
  let compareB: string | null = null;
  let compareResult: CompareResponse | null = null;
  let compareLoading = false;
  let compareError: string | null = null;
  let compareToken = 0;

  // Default to the most useful pair on first open: the best iteration as B
  // (candidate) vs the one before it as A (baseline). The user can re-pick.
  $: if (compareA === null && compareB === null && best !== null) {
    const bestIdx = iterations.findIndex((it) => it.id === best!.id);
    if (bestIdx > 0) {
      compareA = iterations[bestIdx - 1].id;
      compareB = best.id;
    } else if (iterations.length > 1) {
      compareA = iterations[0].id;
      compareB = iterations[1].id;
    }
  }

  async function loadCompare(ws: string, a: string, b: string): Promise<void> {
    // Guard against a stale response overwriting a newer selection: each
    // fetch claims a token; only the latest one is allowed to commit.
    const token = ++compareToken;
    compareLoading = true;
    compareError = null;
    try {
      const result = await api.compare(ws, experimentId, a, b);
      if (token !== compareToken) return;
      compareResult = result;
    } catch (err) {
      if (token !== compareToken) return;
      compareResult = null;
      compareError =
        err instanceof ApiError ? `Compare failed (${err.status}).` : 'Backend unreachable.';
    } finally {
      if (token === compareToken) compareLoading = false;
    }
  }

  $: if (workspaceId && compareA && compareB) {
    void loadCompare(workspaceId, compareA, compareB);
  } else {
    compareResult = null;
    compareError = null;
  }
</script>

<div class="cmp-pickers mb-6">
  <div class="cmp-pick">
    <span class="cmp-pick-role">A · baseline</span>
    <select class="cmp-select" aria-label="Pick iteration A" bind:value={compareA}>
      <option value={null}>— pick —</option>
      {#each iterations as it}
        <option value={it.id}
          >#{it.iteration}{it.primary_metric_value !== null
            ? ` · ${(it.primary_metric_value * 100).toFixed(1)}%`
            : ''}</option
        >
      {/each}
    </select>
  </div>
  <div class="cmp-vs" aria-hidden="true">vs</div>
  <div class="cmp-pick">
    <span class="cmp-pick-role">B · candidate</span>
    <select class="cmp-select" aria-label="Pick iteration B" bind:value={compareB}>
      <option value={null}>— pick —</option>
      {#each iterations as it}
        <option value={it.id}
          >#{it.iteration}{it.primary_metric_value !== null
            ? ` · ${(it.primary_metric_value * 100).toFixed(1)}%`
            : ''}</option
        >
      {/each}
    </select>
  </div>
</div>

{#if !compareA || !compareB}
  <div
    class="rounded-lg border border-dashed border-border bg-surface text-text-3 text-sm py-16 text-center"
  >
    Pick iteration A and B to see what changed and which is better.
  </div>
{:else if compareLoading && !compareResult}
  <div class="rounded-lg border border-border bg-surface text-text-3 text-sm py-16 text-center">
    Computing diff…
  </div>
{:else if compareError}
  <div
    class="rounded-lg border bg-surface text-sm py-12 px-5 text-center"
    style:border-color="var(--color-danger)"
    style:color="var(--color-danger)"
  >
    {compareError}
  </div>
{:else if compareResult}
  {@const r = compareResult}
  <!-- The verdict IS the screen: winner + delta large, evidence below. -->
  <div class="mb-6">
    <CompareVerdict
      recommendation={r.recommendation}
      holdoutStatus={r.holdout_status}
      {targetDirection}
    />
  </div>

  <!-- Metrics diff as stat cards: each metric's B value headlined with its Δ,
       coloured by whether the change is an improvement for the primary metric. -->
  {#if r.metrics_diff.length}
    <section class="mb-6">
      <h2 class="text-xs uppercase tracking-wide text-text-3 mb-3">Metrics · B vs A</h2>
      <div class="metrics-grid">
        {#each r.metrics_diff as row (row.name)}
          <div class="metric-card">
            <DeltaStat
              label={row.name}
              value={row.b}
              delta={row.delta}
              goodWhen={directionFor(row.name)}
              format="plain"
              size="md"
            />
            <span class="metric-from font-mono" data-numeric>from {fmtNumber(row.a)}</span>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <!-- Proposal diff -->
  <section class="mb-6">
    <h2 class="text-xs uppercase tracking-wide text-text-3 mb-2">Parameters</h2>
    <div class="border border-border rounded-lg overflow-hidden bg-surface">
      <table class="w-full text-sm">
        <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
          <tr>
            <th class="text-left px-4 py-2.5 font-medium">Param</th>
            <th class="text-left px-4 py-2.5 font-medium">A</th>
            <th class="text-left px-4 py-2.5 font-medium">B</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each r.proposal_diff as row}
            <tr class={row.changed ? 'bg-surface-2/60' : ''}>
              <td class="px-4 py-2.5 font-mono text-text-2 text-xs">
                {row.key}
                {#if row.changed}
                  <span class="ml-1.5 text-[10px] uppercase tracking-wide text-text-3">changed</span
                  >
                {/if}
              </td>
              <td class="px-4 py-2.5 font-mono text-text-1 text-xs">{row.a}</td>
              <td
                class="px-4 py-2.5 font-mono text-xs"
                style:color={row.changed ? 'var(--color-text-1)' : 'var(--color-text-2)'}
                style:font-weight={row.changed ? '600' : '400'}
              >
                {row.b}
              </td>
            </tr>
          {:else}
            <tr><td class="px-4 py-3 text-text-3 text-xs" colspan="3">No parameters.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>

  <!-- Failure modes -->
  <section class="mb-6">
    <h2 class="text-xs uppercase tracking-wide text-text-3 mb-2">Failure modes</h2>
    {#if Object.keys(r.failure_modes.only_a).length === 0 && Object.keys(r.failure_modes.only_b).length === 0 && Object.keys(r.failure_modes.common).length === 0}
      <div class="rounded-lg border border-border bg-surface px-5 py-4 text-text-3 text-sm">
        No failure modes recorded on either iteration.
      </div>
    {:else}
      <div class="grid grid-cols-3 gap-4">
        <div class="rounded-lg border border-border bg-surface px-4 py-3">
          <div class="text-text-3 text-xs mb-2.5">Gone in B <span class="fm-good">✓</span></div>
          {#each Object.entries(r.failure_modes.only_a) as [mode, count]}
            <div class="fm-row">
              <Pill tone="neutral">{mode}</Pill>
              <span class="font-mono text-text-3" data-numeric>{count}</span>
            </div>
          {:else}
            <span class="text-text-3 text-xs">—</span>
          {/each}
        </div>
        <div class="rounded-lg border border-border bg-surface px-4 py-3">
          <div class="text-text-3 text-xs mb-2.5">New in B <span class="fm-bad">▲</span></div>
          {#each Object.entries(r.failure_modes.only_b) as [mode, count]}
            <div class="fm-row">
              <Pill tone="negative">{mode}</Pill>
              <span class="font-mono text-text-3" data-numeric>{count}</span>
            </div>
          {:else}
            <span class="text-text-3 text-xs">—</span>
          {/each}
        </div>
        <div class="rounded-lg border border-border bg-surface px-4 py-3">
          <div class="text-text-3 text-xs mb-2.5">In both</div>
          {#each Object.entries(r.failure_modes.common) as [mode, counts]}
            <div class="fm-row">
              <Pill tone="caution">{mode}</Pill>
              <span class="font-mono text-text-3" data-numeric>
                {counts[0]} → {counts[1]}
              </span>
            </div>
          {:else}
            <span class="text-text-3 text-xs">—</span>
          {/each}
        </div>
      </div>
    {/if}
  </section>

  <!-- Funnel diff (only when the run recorded a grader funnel) -->
  {#if r.funnel_diff.length > 0}
    <section class="mb-6">
      <h2 class="text-xs uppercase tracking-wide text-text-3 mb-2">Funnel</h2>
      <div class="border border-border rounded-lg overflow-hidden bg-surface">
        <table class="w-full text-sm">
          <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
            <tr>
              <th class="text-left px-4 py-2.5 font-medium">Node</th>
              <th class="text-right px-4 py-2.5 font-medium">A score</th>
              <th class="text-right px-4 py-2.5 font-medium">B score</th>
              <th class="text-right px-4 py-2.5 font-medium">Δ</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border">
            {#each r.funnel_diff as row}
              <tr>
                <td class="px-4 py-2.5 font-mono text-text-2 text-xs">{row.path}</td>
                <td class="px-4 py-2.5 text-right font-mono" data-numeric>{fmtNumber(row.a)}</td>
                <td class="px-4 py-2.5 text-right font-mono" data-numeric>{fmtNumber(row.b)}</td>
                <td
                  class="px-4 py-2.5 text-right font-mono text-xs"
                  style:color={deltaColor(row.delta, targetDirection)}
                  data-numeric
                >
                  {fmtDelta(row.delta)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}
{/if}

<style>
  /* Compare pickers: A vs B side by side with their metric value inline, so the
     choice is informed before the diff even loads. */
  .cmp-pickers {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 0.9rem;
    align-items: center;
  }
  .cmp-pick {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.85rem 1rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
  }
  .cmp-pick-role {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .cmp-select {
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    padding: 0.4rem 0.55rem;
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-md);
    background: var(--color-bg);
    color: var(--color-text-1);
    cursor: pointer;
  }
  .cmp-select:focus-visible {
    outline: 2px solid var(--color-brand);
    outline-offset: 1px;
  }
  .cmp-vs {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    font-style: italic;
  }
  .metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
    gap: 0.75rem;
  }
  .metric-card {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    padding: 0.85rem 1rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
  }
  .metric-from {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .fm-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.2rem 0;
  }
  .fm-good {
    color: var(--color-ok);
  }
  .fm-bad {
    color: var(--color-bad);
  }
</style>

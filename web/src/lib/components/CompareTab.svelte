<!--
  CompareTab: pick two iterations and render the server-computed diff.

  Self-contained and lazy — same molde as PairwisePanel: the parent renders
  it only when the tab is open, and a request token guards against an
  out-of-order response overwriting a newer selection. All delta math is
  server-side (one source shared with the CLI); this only renders it.
-->
<script lang="ts">
  import { api, ApiError, type CompareResponse, type IterationSummary } from '$lib/api/client';
  import { fmtNumber, fmtDelta, deltaColor, recommendationText } from '$lib/viz/format';
  import type { ThresholdDirection } from '$lib/viz/thresholds';

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
  <!-- Recommendation banner: the verdict first, evidence below. -->
  <div
    class="rounded-lg border bg-surface px-5 py-4 mb-6"
    style:border-color={r.recommendation.kind === 'winner'
      ? 'var(--color-success)'
      : 'var(--color-border)'}
  >
    <div class="flex items-center gap-2">
      <span
        class="text-base font-medium"
        style:color={r.recommendation.kind === 'winner'
          ? deltaColor(r.recommendation.delta, targetDirection)
          : 'var(--color-text-1)'}
      >
        {recommendationText(r.recommendation)}
      </span>
    </div>
    {#if r.recommendation.kind === 'winner' && r.recommendation.new_failure_modes.length > 0}
      <div class="text-text-2 text-xs mt-1.5">
        New failure modes:
        {#each r.recommendation.new_failure_modes as m, i}<span class="font-mono"
            >{m}{i < r.recommendation.new_failure_modes.length - 1 ? ', ' : ''}</span
          >{/each}
      </div>
    {/if}
    <!-- Honest holdout caveat: a first-class state, never a fake number. -->
    <div
      class="text-xs mt-2 pt-2 border-t border-dashed border-border"
      style:color="var(--color-text-3)"
      title="The iteration ledger carries no held-out split classification yet."
    >
      {r.holdout_status === 'unavailable'
        ? 'Valid on optimization set · Holdout: not yet tracked'
        : `Holdout: ${r.holdout_status}`}
    </div>
  </div>

  <!-- Metrics diff -->
  <section class="mb-6">
    <h2 class="text-xs uppercase tracking-wide text-text-3 mb-2">Metrics</h2>
    <div class="border border-border rounded-lg overflow-hidden bg-surface">
      <table class="w-full text-sm">
        <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
          <tr>
            <th class="text-left px-4 py-2.5 font-medium">Metric</th>
            <th class="text-right px-4 py-2.5 font-medium">A</th>
            <th class="text-right px-4 py-2.5 font-medium">B</th>
            <th class="text-right px-4 py-2.5 font-medium">Δ</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each r.metrics_diff as row}
            <tr>
              <td class="px-4 py-2.5 font-mono text-text-2 text-xs">{row.name}</td>
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
          {:else}
            <tr><td class="px-4 py-3 text-text-3 text-xs" colspan="4">No metrics.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>

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
          <div class="text-text-3 text-xs mb-2">In A only</div>
          {#each Object.entries(r.failure_modes.only_a) as [mode, count]}
            <div class="flex items-center justify-between text-xs py-0.5">
              <span class="font-mono" style:color="var(--color-danger)">{mode}</span>
              <span class="font-mono text-text-3" data-numeric>{count}</span>
            </div>
          {:else}
            <span class="text-text-3 text-xs">—</span>
          {/each}
        </div>
        <div class="rounded-lg border border-border bg-surface px-4 py-3">
          <div class="text-text-3 text-xs mb-2">In B only</div>
          {#each Object.entries(r.failure_modes.only_b) as [mode, count]}
            <div class="flex items-center justify-between text-xs py-0.5">
              <span class="font-mono" style:color="var(--color-danger)">{mode}</span>
              <span class="font-mono text-text-3" data-numeric>{count}</span>
            </div>
          {:else}
            <span class="text-text-3 text-xs">—</span>
          {/each}
        </div>
        <div class="rounded-lg border border-border bg-surface px-4 py-3">
          <div class="text-text-3 text-xs mb-2">In both</div>
          {#each Object.entries(r.failure_modes.common) as [mode, counts]}
            <div class="flex items-center justify-between text-xs py-0.5">
              <span class="font-mono text-text-2">{mode}</span>
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
</style>

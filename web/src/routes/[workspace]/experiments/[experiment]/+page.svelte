<script lang="ts">
  import { page } from '$app/stores';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import FunnelNode from '$lib/components/FunnelNode.svelte';
  import MetricChip from '$lib/components/MetricChip.svelte';
  import Sparkline from '$lib/components/Sparkline.svelte';
  import { api, ApiError } from '$lib/api/client';
  import type { CompareResponse, FunnelDetail, IterationSummary } from '$lib/api/client';
  import type { PageData } from './$types';

  export let data: PageData;

  // Workspace id from the route, used to build trace viewer hrefs from the
  // iteration drawer ("see what the agent actually did"). The trace endpoint
  // accepts both `tr_...` ids and `run_...` ids, so the run_ids on
  // `IterationSummary.trace_run_ids` link straight through.
  // `[workspace]` is a required route param, so it is always present here.
  $: workspaceId = $page.params.workspace as string;

  type Tab = 'iterations' | 'compare' | 'funnel' | 'decisions';
  let tab: Tab = 'iterations';
  function setTab(id: string) {
    if (id === 'iterations' || id === 'compare' || id === 'funnel' || id === 'decisions')
      tab = id;
  }
  let openIteration: IterationSummary | null = null;

  let compareA: string | null = null;
  let compareB: string | null = null;

  $: summary = data.detail.summary;
  $: iterations = data.detail.iterations;
  $: best = iterations.reduce<IterationSummary | null>((acc, it) => {
    if (it.primary_metric_value === null) return acc;
    if (acc === null || it.primary_metric_value > (acc.primary_metric_value ?? -Infinity))
      return it;
    return acc;
  }, null);

  $: trendValues = iterations
    .map((it) => it.primary_metric_value)
    .filter((v): v is number => v !== null);

  // Compare tab (B3): the diff is computed server-side (one math source
  // shared with the CLI). The FE picks A and B, fetches the structured
  // diff, and renders it. No client-side delta math lives here anymore.
  let compareResult: CompareResponse | null = null;
  let compareLoading = false;
  let compareError: string | null = null;
  let compareToken = 0;

  async function loadCompare(ws: string, a: string, b: string): Promise<void> {
    // Guard against a stale response overwriting a newer selection: each
    // fetch claims a token; only the latest one is allowed to commit.
    const token = ++compareToken;
    compareLoading = true;
    compareError = null;
    try {
      const result = await api.compare(ws, summary.id, a, b);
      if (token !== compareToken) return;
      compareResult = result;
    } catch (err) {
      if (token !== compareToken) return;
      compareResult = null;
      compareError =
        err instanceof ApiError
          ? `Compare failed (${err.status}).`
          : 'Backend unreachable.';
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

  // --- Funnel tab (B2) -----------------------------------------------------
  // The funnel is lazy/additive: we don't load it in +page.server.ts (keeps
  // the page load cheap). It's fetched client-side only when the user picks
  // an iteration. All rollup math lives in the backend; we only render.
  let funnelIterationId: string | null = null;
  let funnelDetail: FunnelDetail | null = null;
  let funnelError: string | null = null;
  let funnelLoading = false;
  // Token guards against an out-of-order response overwriting a newer one.
  let funnelRequest = 0;

  // Default the picker to the best iteration the first time the tab opens.
  $: if (tab === 'funnel' && funnelIterationId === null) {
    const fallback = best ?? iterations[0];
    if (fallback) funnelIterationId = fallback.id;
  }

  $: funnelIteration = iterations.find((it) => it.id === funnelIterationId) ?? null;

  // Fetch whenever the selected iteration changes while the tab is open.
  $: if (tab === 'funnel' && funnelIterationId !== null) {
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

  function fmtNumber(value: number | null, digits = 4): string {
    if (value === null) return '—';
    if (Number.isInteger(value)) return `${value}`;
    return value.toFixed(digits);
  }

  function fmtDelta(value: number | null): string {
    if (value === null) return '—';
    if (Math.abs(value) < 1e-9) return '0';
    const sign = value > 0 ? '+' : '';
    return `${sign}${fmtNumber(value, 3)}`;
  }

  // Same convention as the iterations table Δ column (~line 187): green up,
  // red down, neutral grey for zero/missing.
  function deltaColor(value: number | null): string {
    if (value === null || Math.abs(value) < 1e-9) return 'var(--color-text-3)';
    return value > 0 ? 'var(--color-success)' : 'var(--color-danger)';
  }

  // Recommendation banner copy, derived from the server's verdict.
  function recommendationText(r: CompareResponse['recommendation']): string {
    switch (r.kind) {
      case 'winner':
        return `${r.winner} is better: ${r.metric_name} ${fmtDelta(r.delta)} (${fmtNumber(
          r.a_value
        )} → ${fmtNumber(r.b_value)})`;
      case 'tie':
        return `A and B tie on ${r.metric_name} (${fmtNumber(r.a_value)}) — compare guardrails or failure modes to decide.`;
      case 'different_metric':
        return `Different primary metrics (A=${r.a_metric_name} vs B=${r.b_metric_name}); no recommendation.`;
      default:
        return 'No primary metric to compare.';
    }
  }
</script>

<svelte:head>
  <title>{summary.name} · selfevals</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <nav class="text-xs text-text-3 mb-6 flex items-center gap-1.5" aria-label="Breadcrumb">
    <a class="hover:text-text-1" href={`/${workspaceId}`}>workspace</a>
    <span aria-hidden="true">/</span>
    <a class="hover:text-text-1" href={`/${workspaceId}/experiments`}>experiments</a>
    <span aria-hidden="true">/</span>
    <span class="text-text-2">{summary.name}</span>
  </nav>

  <header class="mb-10">
    <div class="text-xs uppercase tracking-wide text-text-3 mb-2">
      Experiment · {summary.mode}
    </div>
    <h1 class="text-3xl font-semibold tracking-tight">{summary.name}</h1>
    <p class="text-text-2 mt-2 max-w-2xl">{summary.goal}</p>
    <div class="mt-3">
      <CopyableId id={summary.id} label="experiment id" />
    </div>
  </header>

  <section class="grid grid-cols-4 gap-4 mb-10">
    <MetricChip
      label="Best primary"
      value={best?.primary_metric_value ?? null}
      unit={summary.primary_metric}
    />
    <MetricChip
      label="Target"
      value={`${summary.primary_target.operator} ${summary.primary_target.value}`}
      format="plain"
    />
    <MetricChip
      label="Iterations"
      value={`${summary.iteration_count}/${summary.max_iterations}`}
      format="plain"
    />
    <div class="rounded-lg border border-border bg-surface px-4 py-3.5 flex items-center gap-3">
      <div class="flex-1">
        <div class="text-xs uppercase tracking-wide text-text-3 mb-1">
          Trend
        </div>
        <div class="font-mono text-sm text-text-2" data-numeric>
          {trendValues.length} pts
        </div>
      </div>
      <Sparkline values={trendValues} width={88} height={36} />
    </div>
  </section>

  <div class="border-b border-border mb-6">
    <div class="flex gap-6 text-sm">
      {#each [
        { id: 'iterations', label: `Iterations · ${iterations.length}` },
        { id: 'compare', label: 'Compare' },
        { id: 'funnel', label: 'Funnel' },
        { id: 'decisions', label: `Decisions · ${data.decisions.length}` }
      ] as t}
        <button
          type="button"
          class="-mb-px py-2.5 border-b-2 transition-colors {tab === t.id
            ? 'border-text-1 text-text-1 font-medium'
            : 'border-transparent text-text-3 hover:text-text-1'}"
          on:click={() => setTab(t.id)}
        >
          {t.label}
        </button>
      {/each}
    </div>
  </div>

  {#if tab === 'iterations'}
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
            <tr
              class="hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-1 transition-colors cursor-pointer"
              role="button"
              tabindex="0"
              aria-label="Open details for iteration #{it.iteration}"
              on:click={() => (openIteration = it)}
              on:keydown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  openIteration = it;
                }
              }}
            >
              <td class="px-4 py-3 font-mono text-text-3 text-xs" data-numeric>{it.iteration}</td>
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
              <td class="px-4 py-3 text-right font-mono" data-numeric>
                {fmtNumber(it.primary_metric_value)}
              </td>
              <td
                class="px-4 py-3 text-right font-mono text-xs"
                style:color={it.delta_vs_best && it.delta_vs_best > 0
                  ? 'var(--color-success)'
                  : it.delta_vs_best && it.delta_vs_best < 0
                    ? 'var(--color-danger)'
                    : 'var(--color-text-3)'}
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
  {:else if tab === 'compare'}
    <div class="grid grid-cols-2 gap-6 mb-6">
      <div class="rounded-lg border border-border bg-surface px-5 py-4">
        <div class="flex items-center justify-between gap-3">
          <span class="text-xs uppercase tracking-wide text-text-3">
            Iteration A
            <span class="text-text-3 normal-case ml-1">· baseline</span>
          </span>
          <select
            class="font-mono text-xs px-2 py-1 rounded border border-border bg-bg"
            aria-label="Pick iteration A"
            bind:value={compareA}
          >
            <option value={null}>— pick —</option>
            {#each iterations as it}
              <option value={it.id}>#{it.iteration}</option>
            {/each}
          </select>
        </div>
      </div>
      <div class="rounded-lg border border-border bg-surface px-5 py-4">
        <div class="flex items-center justify-between gap-3">
          <span class="text-xs uppercase tracking-wide text-text-3">
            Iteration B
            <span class="text-text-3 normal-case ml-1">· candidate</span>
          </span>
          <select
            class="font-mono text-xs px-2 py-1 rounded border border-border bg-bg"
            aria-label="Pick iteration B"
            bind:value={compareB}
          >
            <option value={null}>— pick —</option>
            {#each iterations as it}
              <option value={it.id}>#{it.iteration}</option>
            {/each}
          </select>
        </div>
      </div>
    </div>

    {#if !compareA || !compareB}
      <div
        class="rounded-lg border border-dashed border-border bg-surface text-text-3 text-sm py-16 text-center"
      >
        Pick iteration A and B to see what changed and which is better.
      </div>
    {:else if compareLoading && !compareResult}
      <div
        class="rounded-lg border border-border bg-surface text-text-3 text-sm py-16 text-center"
      >
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
              ? deltaColor(r.recommendation.delta)
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
                    style:color={deltaColor(row.delta)}
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
                      <span class="ml-1.5 text-[10px] uppercase tracking-wide text-text-3"
                        >changed</span
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
                      style:color={deltaColor(row.delta)}
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
  {:else if tab === 'funnel'}
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
          <div class="mb-2 flex items-baseline justify-between text-[11px] uppercase tracking-wide text-text-3">
            <span>Node</span>
            <span>Mean score</span>
          </div>
          <div class="divide-y divide-border/60">
            {#each funnelKeys as key (key)}
              <FunnelNode node={funnelDetail.nodes[key]} />
            {/each}
          </div>
        {/if}
      </div>
    </div>
  {:else if tab === 'decisions'}
    <ul class="space-y-3">
      {#each data.decisions as d}
        <li class="rounded-lg border border-border bg-surface px-5 py-4">
          <div class="flex items-baseline justify-between gap-4">
            <div class="flex items-baseline gap-3">
              <span class="font-mono text-xs text-text-3" data-numeric>#{d.iteration}</span>
              <DecisionBadge outcome={d.outcome} />
            </div>
            <span class="text-text-3 text-xs font-mono">{new Date(d.created_at).toLocaleString()}</span>
          </div>
          <p class="text-sm text-text-2 mt-2">{d.automated_rationale}</p>
        </li>
      {/each}
    </ul>
  {/if}
</div>

{#if openIteration}
  <div
    class="fixed inset-0 bg-black/20 z-40"
    role="button"
    tabindex="0"
    on:click={() => (openIteration = null)}
    on:keydown={(e) => e.key === 'Escape' && (openIteration = null)}
  ></div>
  <aside
    class="fixed top-0 right-0 h-full w-[480px] bg-surface border-l border-border z-50 overflow-y-auto px-6 py-7 shadow-2"
  >
    <div class="flex items-baseline justify-between mb-6">
      <h2 class="text-lg font-semibold">Iteration #{openIteration.iteration}</h2>
      <button
        type="button"
        on:click={() => (openIteration = null)}
        class="text-text-3 hover:text-text-1 text-sm"
      >
        Close (Esc)
      </button>
    </div>

    <dl class="space-y-4 text-sm">
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Hypothesis</dt>
        <dd>{openIteration.hypothesis}</dd>
      </div>
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Parameters</dt>
        <dd>
          <pre class="font-mono text-xs bg-surface-2 rounded p-3 overflow-x-auto">{JSON.stringify(openIteration.proposed_parameters, null, 2)}</pre>
        </dd>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <dt class="text-text-3 text-xs mb-0.5">Primary metric</dt>
          <dd class="font-mono" data-numeric>{fmtNumber(openIteration.primary_metric_value)}</dd>
        </div>
        <div>
          <dt class="text-text-3 text-xs mb-0.5">Δ vs running best</dt>
          <dd class="font-mono" data-numeric>{fmtDelta(openIteration.delta_vs_best)}</dd>
        </div>
        <div>
          <dt class="text-text-3 text-xs mb-0.5">Cost (USD)</dt>
          <dd class="font-mono" data-numeric>{openIteration.cost_usd ?? '—'}</dd>
        </div>
        <div>
          <dt class="text-text-3 text-xs mb-0.5">Duration</dt>
          <dd class="font-mono" data-numeric>{openIteration.duration_seconds ?? '—'}s</dd>
        </div>
      </div>
      <div>
        <dt class="text-text-3 text-xs mb-0.5">Decision</dt>
        <dd class="flex items-center gap-2">
          <DecisionBadge outcome={openIteration.decision_outcome} />
          <span class="text-text-2">{openIteration.decision_rationale ?? '—'}</span>
        </dd>
      </div>
      <div>
        <dt class="text-text-3 text-xs mb-1.5">
          Traces
          <span class="text-text-3 font-mono normal-case ml-1">
            · {openIteration.trace_run_ids.length}
          </span>
        </dt>
        <dd>
          {#if openIteration.trace_run_ids.length === 0}
            <span class="text-text-3 text-xs">No traces persisted for this iteration.</span>
          {:else}
            <ul class="space-y-1">
              {#each openIteration.trace_run_ids as runId, idx}
                <li class="flex items-center gap-2">
                  <a
                    href={`/${workspaceId}/traces/${runId}`}
                    on:click={() => (openIteration = null)}
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
        <dd><CopyableId id={openIteration.id} label="iteration record id" /></dd>
      </div>
    </dl>
  </aside>
{/if}

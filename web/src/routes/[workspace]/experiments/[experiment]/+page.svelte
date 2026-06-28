<script lang="ts">
  import { page } from '$app/stores';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import FunnelNode from '$lib/components/FunnelNode.svelte';
  import { api, ApiError } from '$lib/api/client';
  import { openTraceStream, type StreamHandle } from '$lib/api/sse';
  import type {
    CompareResponse,
    ExperimentResults,
    FunnelDetail,
    IterationSummary
  } from '$lib/api/client';
  import CaseResultRow from '$lib/components/CaseResultRow.svelte';
  import type { PageData } from './$types';
  import { onDestroy } from 'svelte';
  import { invalidateAll } from '$app/navigation';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import BaselinePanel from '$lib/components/BaselinePanel.svelte';
  import PairwisePanel from '$lib/components/PairwisePanel.svelte';
  import Tabs from '$lib/components/ui/Tabs.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import { LineChart, StatRing, CountUp } from '$lib/components/charts';
  import {
    directionFromOperator,
    thresholdLevel,
    levelColor,
    deltaLevel
  } from '$lib/viz/thresholds';

  export let data: PageData;

  // Workspace id from the route, used to build trace viewer hrefs from the
  // iteration drawer ("see what the agent actually did"). The trace endpoint
  // accepts both `tr_...` ids and `run_...` ids, so the run_ids on
  // `IterationSummary.trace_run_ids` link straight through.
  // `[workspace]` is a required route param, so it is always present here.
  $: workspaceId = $page.params.workspace as string;

  type Tab = 'iterations' | 'results' | 'compare' | 'funnel' | 'pairwise' | 'decisions';
  let tab: Tab = 'iterations';
  function setTab(id: string) {
    if (
      id === 'iterations' ||
      id === 'results' ||
      id === 'compare' ||
      id === 'funnel' ||
      id === 'pairwise' ||
      id === 'decisions'
    )
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

  // The optimization story: primary metric per iteration, in run order, as
  // chart points (x = iteration index). This is the "accuracy climbing" line —
  // coloured against the experiment's own target so the threshold line shows
  // exactly where "good enough" sits.
  $: targetDirection = directionFromOperator(summary.primary_target.operator);
  $: accuracyPoints = iterations
    .filter((it) => it.primary_metric_value !== null)
    .map((it) => ({ x: it.iteration, y: it.primary_metric_value as number }));
  $: bestValue = best?.primary_metric_value ?? null;
  $: bestLevel = thresholdLevel(bestValue, {
    target: summary.primary_target.value,
    direction: targetDirection
  });

  // --- Live run state: cancel + poll -------------------------------------
  // While a run is queued/running we poll the experiment so iterations and
  // state climb without a manual refresh — same lightweight cadence as
  // ActiveRunsPill. Polling stops the moment the run reaches a terminal state.
  const ACTIVE_STATES = new Set(['queued', 'running', 'draft']);
  $: isActive = ACTIVE_STATES.has(summary.state);

  let showCancel = false;
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  function startPoll() {
    if (pollTimer || typeof window === 'undefined') return;
    pollTimer = setInterval(() => void invalidateAll(), 2500);
  }
  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
  $: if (isActive) startPoll();
  else stopPoll();
  onDestroy(stopPoll);

  async function cancelRun() {
    try {
      await api.cancelExperiment(workspaceId, summary.id);
      toast.success('Cancel requested', 'The run will stop after the current step.');
      await invalidateAll();
    } catch (err) {
      toast.error('Cancel failed', err instanceof ApiError ? err.detail : String(err));
    }
  }

  // --- Live run: stream spans as they land --------------------------------
  // While the experiment is active we look up the live run id from /runs/active
  // (filtered to this workspace) and subscribe to its span stream, so the page
  // shows real motion — span count climbing, last span name — instead of a
  // static "running" badge. The trace viewer owns the full tree; here we want a
  // pulse of life and a one-click jump to it. Everything tears down on
  // complete / when the run leaves the active set / on navigate away.
  let liveRunId: string | null = null;
  let liveSpanCount = 0;
  let liveLastSpan: string | null = null;
  let liveStream: StreamHandle | null = null;
  let liveLookupTimer: ReturnType<typeof setInterval> | null = null;

  async function findLiveRun(): Promise<void> {
    try {
      const { runs } = await api.activeRuns();
      const mine = runs.find((r) => r.workspace_id === workspaceId);
      if (mine && mine.run_id !== liveRunId) attachLive(mine.run_id);
      else if (!mine) detachLive();
    } catch {
      /* transient — keep the last known live state */
    }
  }

  function attachLive(runId: string): void {
    detachLive();
    liveRunId = runId;
    liveSpanCount = 0;
    liveLastSpan = null;
    liveStream = openTraceStream(workspaceId, runId, {
      onSnapshot: (trace) => {
        liveSpanCount = trace.spans?.length ?? 0;
      },
      onSpan: (span) => {
        liveSpanCount += 1;
        liveLastSpan = span.name ?? span.kind ?? 'span';
      },
      onComplete: () => detachLive()
    });
  }

  function detachLive(): void {
    liveStream?.close();
    liveStream = null;
    liveRunId = null;
  }

  // Poll for the live run id only while active; the SSE itself is push-based.
  $: if (isActive) startLiveLookup();
  else stopLiveLookup();

  function startLiveLookup(): void {
    if (liveLookupTimer || typeof window === 'undefined') return;
    void findLiveRun();
    liveLookupTimer = setInterval(() => void findLiveRun(), 3000);
  }
  function stopLiveLookup(): void {
    if (liveLookupTimer) {
      clearInterval(liveLookupTimer);
      liveLookupTimer = null;
    }
    detachLive();
  }
  onDestroy(stopLiveLookup);

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

  // --- Results tab: per-case expected vs detected vs matched ---------------
  // Lazy like the funnel — the per-scenario grid can be large, so it stays off
  // the server load and is fetched only when the tab opens. `includeTurns`
  // re-fetches with per-turn breakdowns for conversation cases; a token guard
  // keeps an out-of-order response from clobbering a newer one.
  let resultsData: ExperimentResults | null = null;
  let resultsError: string | null = null;
  let resultsLoading = false;
  let resultsIncludeTurns = false;
  let resultsRequest = 0;

  $: if (tab === 'results') {
    void loadResults(resultsIncludeTurns);
  }

  async function loadResults(includeTurns: boolean): Promise<void> {
    const token = ++resultsRequest;
    resultsLoading = true;
    resultsError = null;
    try {
      const detail = await api.experimentResults(workspaceId, summary.id, { includeTurns });
      if (token !== resultsRequest) return;
      resultsData = detail;
    } catch (err) {
      if (token !== resultsRequest) return;
      resultsData = null;
      resultsError =
        err instanceof ApiError && err.status === 404
          ? 'No results yet for this experiment.'
          : 'Could not load per-case results.';
    } finally {
      if (token === resultsRequest) resultsLoading = false;
    }
  }

  function toggleResultTurns(): void {
    resultsIncludeTurns = !resultsIncludeTurns;
    void loadResults(resultsIncludeTurns);
  }

  // A case carries a multi-turn conversation when it has any persisted turns.
  $: resultsHasConversations = (resultsData?.cases ?? []).some((c) => c.turns.length > 0);
  $: resultsPassCount = (resultsData?.cases ?? []).filter((c) => c.matched === true).length;

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

  <header class="mb-10 flex items-start justify-between gap-6">
    <div class="min-w-0">
      <div class="text-xs uppercase tracking-wide text-text-3 mb-2">
        Experiment · {summary.mode}
      </div>
      <h1 class="text-3xl font-semibold tracking-tight">{summary.name}</h1>
      <p class="text-text-2 mt-2 max-w-2xl">{summary.goal}</p>
      <div class="mt-3">
        <CopyableId id={summary.id} label="experiment id" />
      </div>
    </div>
    <div class="flex shrink-0 items-center gap-3">
      <span class="state-pill">
        <StatusDot state={summary.state} />
        <span class="state-pill-label">{summary.state}</span>
      </span>
      <Button
        variant="secondary"
        size="sm"
        href={`/${workspaceId}/experiments/${summary.id}/analyze`}
      >
        Analyze failures
      </Button>
      {#if isActive}
        <Button variant="danger" size="sm" on:click={() => (showCancel = true)}>Cancel run</Button>
      {/if}
    </div>
  </header>

  {#if isActive}
    <!-- Live header: the run is breathing. A pulsing dot, the iteration it's on,
         the metric counting up as iterations land, and the agent's last action
         streaming in from SSE. This is the "watch a run live" moment. -->
    <div class="live mb-8" class:live-attached={liveRunId}>
      <div class="live-main">
        <span class="live-dot" aria-hidden="true"></span>
        <span class="live-title">Run in progress</span>
        <span class="live-sep" aria-hidden="true">·</span>
        <span class="live-iter mono" data-numeric>
          iteration {summary.iteration_count}<span class="live-iter-of"
            >/{summary.max_iterations}</span
          >
        </span>
        {#if bestValue !== null}
          <span class="live-sep" aria-hidden="true">·</span>
          <span class="live-metric">
            <span class="live-metric-label">{summary.primary_metric}</span>
            <span class="live-metric-val" style:color={levelColor(bestLevel)}>
              <CountUp value={bestValue} format="percent" />
            </span>
          </span>
        {/if}
      </div>

      <div class="live-foot">
        {#if liveRunId}
          <span class="live-activity">
            <span class="live-activity-count mono" data-numeric>{liveSpanCount}</span>
            <span class="live-activity-label"
              >span{liveSpanCount === 1 ? '' : 's'}</span
            >
            {#if liveLastSpan}
              <span class="live-sep" aria-hidden="true">·</span>
              <span class="live-last">{liveLastSpan}</span>
            {/if}
          </span>
          <a class="watch-link" href={`/${workspaceId}/traces/${liveRunId}`}>Watch live →</a>
        {:else}
          <span class="live-waiting">waiting for the run to emit spans…</span>
        {/if}
      </div>

      <!-- Progress of the optimization loop across its iteration budget. -->
      <div class="live-progress" aria-hidden="true">
        <div
          class="live-progress-fill"
          style:width="{Math.min(
            100,
            (summary.iteration_count / Math.max(1, summary.max_iterations)) * 100
          )}%"
        ></div>
      </div>
    </div>
  {/if}

  <!-- The optimization at a glance: the line is the primary metric climbing
       across iterations, with the target drawn in; the ring is where the best
       iteration landed against that target. Green/amber/red is derived, never
       hardcoded — the same threshold language as everywhere else. -->
  <section class="analytics mb-10">
    <div class="analytics-chart card">
      <div class="analytics-head">
        <div>
          <div class="analytics-eyebrow">Primary metric over iterations</div>
          <div class="analytics-metric mono">{summary.primary_metric}</div>
        </div>
        <div class="analytics-target mono">
          target {summary.primary_target.operator}
          {summary.primary_target.value}
        </div>
      </div>
      {#if accuracyPoints.length > 0}
        <LineChart
          points={accuracyPoints}
          height={150}
          format="percent"
          threshold={{ target: summary.primary_target.value, direction: targetDirection }}
        />
      {:else}
        <div class="analytics-empty">No completed iterations yet.</div>
      {/if}
    </div>

    <div class="analytics-side">
      <div class="card analytics-ring">
        <StatRing
          value={bestValue}
          threshold={{ target: summary.primary_target.value, direction: targetDirection }}
          label="Best"
          size={84}
        />
      </div>
      <div class="card analytics-stat">
        <span class="analytics-stat-label">Iterations</span>
        <span class="analytics-stat-value mono" data-numeric>
          {summary.iteration_count}<span class="analytics-stat-of">/{summary.max_iterations}</span>
        </span>
      </div>
    </div>
  </section>

  <div class="mb-6">
    <Tabs
      tabs={[
        { id: 'iterations', label: `Iterations · ${iterations.length}` },
        { id: 'results', label: 'Results' },
        { id: 'compare', label: 'Compare' },
        { id: 'funnel', label: 'Funnel' },
        { id: 'pairwise', label: 'Pairwise' },
        { id: 'decisions', label: `Decisions · ${data.decisions.length}` }
      ]}
      active={tab}
      on:change={(e) => setTab(e.detail)}
    />
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
            {@const isBest = best !== null && it.id === best.id}
            <tr
              class="iter-row hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-1 transition-colors cursor-pointer"
              class:iter-best={isBest}
              role="button"
              tabindex="0"
              aria-label="Open details for iteration #{it.iteration}{isBest ? ' (best so far)' : ''}"
              on:click={() => (openIteration = it)}
              on:keydown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  openIteration = it;
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
                    target: summary.primary_target.value,
                    direction: targetDirection
                  })
                )}
                data-numeric
              >
                {fmtNumber(it.primary_metric_value)}
              </td>
              <td
                class="px-4 py-3 text-right font-mono text-xs"
                style:color={levelColor(deltaLevel(it.delta_vs_best, targetDirection))}
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
  {:else if tab === 'results'}
    <section>
      <div class="flex items-baseline justify-between mb-4">
        <div class="flex items-baseline gap-3">
          <h2 class="text-lg font-semibold">Per-case results</h2>
          {#if resultsData}
            <span class="text-xs text-text-3 font-mono" data-numeric>
              {resultsPassCount}/{resultsData.total} passed
              {#if resultsData.iteration !== null}· iter {resultsData.iteration}{/if}
            </span>
          {/if}
        </div>
        {#if resultsHasConversations}
          <button
            type="button"
            class="text-xs text-text-2 underline-offset-2 hover:text-text-1 hover:underline"
            on:click={toggleResultTurns}
          >
            {resultsIncludeTurns ? 'Hide turns' : 'Expand turns'}
          </button>
        {/if}
      </div>

      {#if resultsLoading && !resultsData}
        <div class="space-y-3">
          {#each Array(3) as _}
            <div class="h-20 rounded-md border border-border bg-surface animate-pulse"></div>
          {/each}
        </div>
      {:else if resultsError}
        <div class="rounded-lg border border-border bg-surface px-6 py-8 text-center text-text-2">
          {resultsError}
        </div>
      {:else if resultsData && resultsData.cases.length === 0}
        <div class="rounded-lg border border-border bg-surface px-6 py-12 text-center text-text-2">
          No cases recorded for the best iteration yet.
        </div>
      {:else if resultsData}
        <div class="space-y-3" class:opacity-60={resultsLoading}>
          {#each resultsData.cases as c (c.case_id)}
            <CaseResultRow result={c} {workspaceId} />
          {/each}
        </div>
      {/if}
    </section>
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
                    <td class="px-4 py-2.5 text-right font-mono" data-numeric>{fmtNumber(row.a)}</td
                    >
                    <td class="px-4 py-2.5 text-right font-mono" data-numeric>{fmtNumber(row.b)}</td
                    >
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
          <div
            class="mb-2 flex items-baseline justify-between text-[11px] uppercase tracking-wide text-text-3"
          >
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
  {:else if tab === 'pairwise'}
    <PairwisePanel {workspaceId} experimentId={summary.id} />
  {:else if tab === 'decisions'}
    <ul class="space-y-3">
      {#each data.decisions as d}
        <li class="rounded-lg border border-border bg-surface px-5 py-4">
          <div class="flex items-baseline justify-between gap-4">
            <div class="flex items-baseline gap-3">
              <span class="font-mono text-xs text-text-3" data-numeric>#{d.iteration}</span>
              <DecisionBadge outcome={d.outcome} />
            </div>
            <span class="text-text-3 text-xs font-mono"
              >{new Date(d.created_at).toLocaleString()}</span
            >
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
          <pre class="font-mono text-xs bg-surface-2 rounded p-3 overflow-x-auto">{JSON.stringify(
              openIteration.proposed_parameters,
              null,
              2
            )}</pre>
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
      {#if openIteration.primary_metric_value !== null}
        <div class="pt-2 border-t border-border">
          <dt class="text-text-3 text-xs mb-2 uppercase tracking-wide">
            Baseline &amp; regression
          </dt>
          <dd>
            <BaselinePanel {workspaceId} iterationId={openIteration.id} datasets={data.datasets} />
          </dd>
        </div>
      {/if}
    </dl>
  </aside>
{/if}

<ConfirmDialog
  open={showCancel}
  title="Cancel this run?"
  message="The run stops after the current step finishes. Iterations already completed are kept."
  confirmLabel="Cancel run"
  cancelLabel="Keep running"
  tone="danger"
  onConfirm={cancelRun}
  on:close={() => (showCancel = false)}
/>

<style>
  .state-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.3rem 0.65rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  .state-pill-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
    text-transform: capitalize;
  }

  /* Analytics row: the climbing-metric chart takes the lead width, the ring +
     iteration count sit beside it. This is the first thing the eye lands on. */
  .analytics {
    display: grid;
    grid-template-columns: 1fr 200px;
    gap: 1rem;
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .analytics-chart {
    padding: 1rem 1.2rem 0.6rem;
    min-width: 0;
  }
  .analytics-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .analytics-eyebrow {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .analytics-metric {
    font-size: var(--text-sm);
    color: var(--color-text-1);
    margin-top: 0.15rem;
  }
  .analytics-target {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .analytics-empty {
    padding: 2.5rem 0;
    text-align: center;
    font-size: var(--text-sm);
    color: var(--color-text-3);
  }
  .analytics-side {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .analytics-ring {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0.8rem;
    flex: 1;
  }
  .analytics-stat {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    padding: 0.8rem 1rem;
  }
  .analytics-stat-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .analytics-stat-value {
    font-size: var(--text-xl);
    font-weight: 500;
    line-height: 1;
  }
  .analytics-stat-of {
    color: var(--color-text-3);
    font-size: var(--text-md);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.3;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .live-dot,
    .live-dot::after {
      animation: none;
    }
  }

  /* Live header — brand-tinted, the one place the chromatic accent signals
     "this is happening now". */
  .live {
    border: 1px solid color-mix(in srgb, var(--color-brand) 30%, var(--color-border));
    border-radius: var(--radius-lg);
    background: var(--color-brand-subtle);
    overflow: hidden;
  }
  .live-main {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.75rem 1rem 0.5rem;
  }
  .live-dot {
    position: relative;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-brand);
    flex-shrink: 0;
  }
  /* A second expanding ring for a richer "live" pulse than a simple fade. */
  .live-dot::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--color-brand);
    animation: live-ping 1.8s ease-out infinite;
  }
  @keyframes live-ping {
    0% {
      transform: scale(1);
      opacity: 0.5;
    }
    100% {
      transform: scale(3);
      opacity: 0;
    }
  }
  .live-title {
    font-weight: 600;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .live-sep {
    color: var(--color-text-3);
  }
  .live-iter {
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .live-iter-of {
    color: var(--color-text-3);
  }
  .live-metric {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35rem;
  }
  .live-metric-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .live-metric-val {
    font-size: var(--text-sm);
    font-weight: 600;
  }
  .live-foot {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0 1rem 0.7rem;
  }
  .live-activity {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .live-activity-count {
    color: var(--color-text-1);
    font-weight: 500;
  }
  .live-activity-label,
  .live-last {
    color: var(--color-text-3);
  }
  .live-waiting {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    font-style: italic;
  }
  .watch-link {
    margin-left: auto;
    font-size: var(--text-xs);
    font-weight: 500;
    color: var(--color-brand-strong);
    text-underline-offset: 2px;
  }
  .watch-link:hover {
    text-decoration: underline;
  }
  .live-progress {
    height: 3px;
    background: color-mix(in srgb, var(--color-brand) 15%, transparent);
  }
  .live-progress-fill {
    height: 100%;
    background: var(--color-brand);
    transition: width var(--dur-slow) var(--ease-out);
  }

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

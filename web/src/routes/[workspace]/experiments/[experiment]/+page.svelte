<script lang="ts">
  import { page } from '$app/stores';
  import { invalidateAll } from '$app/navigation';
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import { api, ApiError } from '$lib/api/client';
  import type { IterationSummary } from '$lib/api/client';
  import type { PageData } from './$types';
  import { toast } from '$lib/stores/toasts';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import PairwisePanel from '$lib/components/PairwisePanel.svelte';
  import Tabs from '$lib/components/ui/Tabs.svelte';
  import AnalyticsHeader from '$lib/components/AnalyticsHeader.svelte';
  import ExperimentHeader from '$lib/components/ExperimentHeader.svelte';
  import IterationsTable from '$lib/components/IterationsTable.svelte';
  import IterationDrawer from '$lib/components/IterationDrawer.svelte';
  import CompareTab from '$lib/components/CompareTab.svelte';
  import FunnelTab from '$lib/components/FunnelTab.svelte';
  import ResultsTab from '$lib/components/ResultsTab.svelte';
  import LiveRunHeader from '$lib/components/LiveRunHeader.svelte';
  import { directionFromOperator, thresholdLevel } from '$lib/viz/thresholds';
  import { crumbLabels } from '$lib/nav/breadcrumbs';

  export let data: PageData;

  // Feed the global topbar breadcrumb the experiment's name in place of its id,
  // keyed by the raw path segment (the experiment id). Reactive so it updates
  // when navigating between experiments without a full remount.
  $: crumbLabels.set({ [$page.params.experiment as string]: data.detail.summary.name });

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

  $: summary = data.detail.summary;
  $: iterations = data.detail.iterations;
  $: best = iterations.reduce<IterationSummary | null>((acc, it) => {
    if (it.primary_metric_value === null) return acc;
    if (acc === null || it.primary_metric_value > (acc.primary_metric_value ?? -Infinity))
      return it;
    return acc;
  }, null);

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

  // --- Live run state: cancel ----------------------------------------------
  const ACTIVE_STATES = new Set(['queued', 'running', 'draft']);
  $: isActive = ACTIVE_STATES.has(summary.state);

  let showCancel = false;

  async function cancelRun() {
    try {
      await api.cancelExperiment(workspaceId, summary.id);
      toast.success('Cancel requested', 'The run will stop after the current step.');
      await invalidateAll();
    } catch (err) {
      toast.error('Cancel failed', err instanceof ApiError ? err.detail : String(err));
    }
  }
</script>

<svelte:head>
  <title>{summary.name} · selfevals</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <ExperimentHeader
    {workspaceId}
    name={summary.name}
    goal={summary.goal}
    mode={summary.mode}
    state={summary.state}
    experimentId={summary.id}
    {isActive}
    on:cancel={() => (showCancel = true)}
  />

  {#if isActive}
    <!-- Live header: the run is breathing. A pulsing dot, the iteration it's on,
         the metric counting up as iterations land, and the agent's last action
         streaming in from SSE. This is the "watch a run live" moment. -->
    <LiveRunHeader
      {workspaceId}
      {isActive}
      iterationCount={summary.iteration_count}
      maxIterations={summary.max_iterations}
      primaryMetric={summary.primary_metric}
      {bestValue}
      {bestLevel}
    />
  {/if}

  <AnalyticsHeader
    primaryMetric={summary.primary_metric}
    targetOperator={summary.primary_target.operator}
    targetValue={summary.primary_target.value}
    {targetDirection}
    {accuracyPoints}
    {bestValue}
    iterationCount={summary.iteration_count}
    maxIterations={summary.max_iterations}
  />

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
    <IterationsTable
      {iterations}
      {best}
      targetValue={summary.primary_target.value}
      {targetDirection}
      on:select={(e) => (openIteration = e.detail)}
    />
  {:else if tab === 'results'}
    <ResultsTab {workspaceId} experimentId={summary.id} />
  {:else if tab === 'compare'}
    <CompareTab {workspaceId} experimentId={summary.id} {iterations} {best} {targetDirection} />
  {:else if tab === 'funnel'}
    <FunnelTab {workspaceId} {iterations} {best} />
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
  <IterationDrawer
    iteration={openIteration}
    {workspaceId}
    datasets={data.datasets}
    on:close={() => (openIteration = null)}
  />
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

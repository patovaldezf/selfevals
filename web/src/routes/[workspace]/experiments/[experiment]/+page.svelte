<script lang="ts">
  import { page } from '$app/stores';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import MetricChip from '$lib/components/MetricChip.svelte';
  import Sparkline from '$lib/components/Sparkline.svelte';
  import type { IterationSummary } from '$lib/api/client';
  import type { PageData } from './$types';

  export let data: PageData;

  // Workspace id from the route, used to build trace viewer hrefs from the
  // iteration drawer ("see what the agent actually did"). The trace endpoint
  // accepts both `tr_...` ids and `run_...` ids, so the run_ids on
  // `IterationSummary.trace_run_ids` link straight through.
  $: workspaceId = $page.params.workspace;

  type Tab = 'iterations' | 'compare' | 'decisions';
  let tab: Tab = 'iterations';
  function setTab(id: string) {
    if (id === 'iterations' || id === 'compare' || id === 'decisions') tab = id;
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

  $: itA = iterations.find((it) => it.id === compareA) ?? null;
  $: itB = iterations.find((it) => it.id === compareB) ?? null;

  const setA = (v: string) => (compareA = v);
  const setB = (v: string) => (compareB = v);
  $: compareCols = [
    { which: 'A', value: compareA, set: setA, it: itA },
    { which: 'B', value: compareB, set: setB, it: itB }
  ];

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
    <div class="grid grid-cols-2 gap-6">
      {#each compareCols as col}
        <div class="rounded-lg border border-border bg-surface p-5">
          <div class="flex items-center justify-between mb-4">
            <span class="text-xs uppercase tracking-wide text-text-3">Iteration {col.which}</span>
            <select
              class="font-mono text-xs px-2 py-1 rounded border border-border bg-bg"
              value={col.which === 'A' ? compareA : compareB}
              on:change={(e) => col.set(e.currentTarget.value)}
            >
              <option value="">— pick —</option>
              {#each iterations as it}
                <option value={it.id}>#{it.iteration}</option>
              {/each}
            </select>
          </div>
          {#if col.it}
            <div class="space-y-3 text-sm">
              <div>
                <div class="text-text-3 text-xs mb-0.5">Hypothesis</div>
                <div class="text-text-1">{col.it.hypothesis}</div>
              </div>
              <div>
                <div class="text-text-3 text-xs mb-0.5">Parameters</div>
                <pre class="font-mono text-xs bg-surface-2 rounded p-2.5 overflow-x-auto">{JSON.stringify(col.it.proposed_parameters, null, 2)}</pre>
              </div>
              <div class="flex gap-6">
                <div>
                  <div class="text-text-3 text-xs mb-0.5">Primary</div>
                  <div class="font-mono" data-numeric>
                    {fmtNumber(col.it.primary_metric_value)}
                  </div>
                </div>
                <div>
                  <div class="text-text-3 text-xs mb-0.5">Decision</div>
                  <DecisionBadge outcome={col.it.decision_outcome} />
                </div>
              </div>
            </div>
          {:else}
            <div class="text-text-3 text-sm py-12 text-center">Select an iteration.</div>
          {/if}
        </div>
      {/each}
    </div>
    {#if itA && itB && itA.primary_metric_value !== null && itB.primary_metric_value !== null}
      <div class="mt-6 rounded-lg border border-border bg-surface px-5 py-4 text-sm">
        <div class="text-text-3 text-xs mb-1">Δ B − A</div>
        <div
          class="font-mono text-xl"
          style:color={itB.primary_metric_value > itA.primary_metric_value
            ? 'var(--color-success)'
            : itB.primary_metric_value < itA.primary_metric_value
              ? 'var(--color-danger)'
              : 'var(--color-text-1)'}
          data-numeric
        >
          {fmtDelta(itB.primary_metric_value - itA.primary_metric_value)}
        </div>
      </div>
    {/if}
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

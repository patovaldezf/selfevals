<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../../$types';
  import { invalidateAll } from '$app/navigation';
  import { api, ApiError } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import BarChart from '$lib/components/charts/BarChart.svelte';
  import CaseRow from '$lib/components/CaseRow.svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import { Lock } from 'lucide-svelte';

  export let data: PageData & LayoutData;

  $: ds = data.dataset;
  $: stats = ds.statistics;
  $: isFrozen = ds.status === 'frozen';
  $: baseline = data.baseline;

  let showFreeze = false;

  // Turn a facet dict into sorted bars for the BarChart.
  function bars(facet: Record<string, number> | undefined) {
    if (!facet) return [];
    return Object.entries(facet)
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
  }

  // Named facets so each entry keeps its `Record<string, number>` type (a
  // mixed [string, dict] tuple literal would widen to a union).
  $: facets = stats
    ? ([
        { title: 'By level', data: stats.by_level },
        { title: 'By feature', data: stats.by_feature },
        { title: 'By source', data: stats.by_source },
        { title: 'By risk', data: stats.by_risk }
      ] as const)
    : [];

  // Split allocation as visual segments — optimization / holdout / reliability
  // are what the proposer can train on vs what's held back to detect overfit.
  // Seeing the proportions beats reading three separate percentages.
  $: splitSegments = [
    {
      key: 'optimization',
      label: 'Optimization',
      value: ds.split_allocation.optimization,
      color: 'var(--color-brand)'
    },
    {
      key: 'holdout',
      label: 'Holdout',
      value: ds.split_allocation.holdout,
      color: 'var(--color-chart-2)'
    },
    {
      key: 'reliability',
      label: 'Reliability',
      value: ds.split_allocation.reliability,
      color: 'var(--color-chart-3)'
    }
  ].filter((s) => s.value > 0);

  async function freeze() {
    try {
      await api.freezeDataset(data.workspace.id, ds.id);
      toast.success('Dataset frozen', 'Manifest hash recomputed; the set is now immutable.');
      await invalidateAll();
    } catch (err) {
      toast.error('Freeze failed', err instanceof ApiError ? err.detail : String(err));
    }
  }
</script>

<svelte:head>
  <title>{ds.name} · Datasets</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <nav class="text-xs text-text-3 mb-6 flex items-center gap-1.5" aria-label="Breadcrumb">
    <a class="hover:text-text-1" href={`/${data.workspace.id}/datasets`}>datasets</a>
    <span aria-hidden="true">/</span>
    <span class="text-text-2">{ds.name}</span>
  </nav>

  <header class="mb-8 flex items-start justify-between gap-6">
    <div class="min-w-0">
      <div class="text-xs uppercase tracking-wide text-text-3 mb-2">
        Dataset · {ds.dataset_type}
      </div>
      <h1 class="text-3xl font-semibold tracking-tight">{ds.name}</h1>
      {#if ds.description}
        <p class="text-text-2 mt-2 max-w-2xl">{ds.description}</p>
      {/if}
      <div class="mt-3 flex items-center gap-3">
        <CopyableId id={ds.id} label="dataset id" />
        {#if ds.manifest_hash}
          <span class="font-mono text-xs text-text-3">manifest {ds.manifest_hash.slice(0, 12)}</span
          >
        {/if}
      </div>
    </div>
    <div class="shrink-0">
      {#if isFrozen}
        <Badge tone="brand" icon={Lock}>frozen</Badge>
      {:else}
        <Button variant="secondary" on:click={() => (showFreeze = true)}>Freeze</Button>
      {/if}
    </div>
  </header>

  <!-- Counts + split allocation as a single proportional bar. -->
  <section class="split-section">
    <div class="split-counts">
      <div class="split-count">
        <span class="split-count-val mono" data-numeric>{ds.case_count}</span>
        <span class="split-count-label">cases</span>
      </div>
      <div class="split-count">
        <span class="split-count-val mono" data-numeric>{stats?.holdout_count ?? 0}</span>
        <span class="split-count-label">held out</span>
      </div>
    </div>
    <div class="split-alloc">
      <div class="split-bar" role="img" aria-label="Split allocation">
        {#each splitSegments as seg (seg.key)}
          <div
            class="split-seg"
            style:width="{seg.value * 100}%"
            style:background={seg.color}
            title="{seg.label} {(seg.value * 100).toFixed(0)}%"
          ></div>
        {/each}
      </div>
      <div class="split-legend">
        {#each splitSegments as seg (seg.key)}
          <span class="split-leg">
            <span class="split-dot" style:background={seg.color}></span>
            <span class="split-leg-label">{seg.label}</span>
            <span class="split-leg-val mono" data-numeric>{(seg.value * 100).toFixed(0)}%</span>
          </span>
        {/each}
      </div>
    </div>
  </section>

  <!-- Facets -->
  {#if stats}
    <section class="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
      {#each facets as facet}
        {#if Object.keys(facet.data ?? {}).length}
          <div class="rounded-lg border border-border bg-surface p-5">
            <h2 class="mb-4 text-sm font-semibold">{facet.title}</h2>
            <BarChart data={bars(facet.data)} format="count" color="var(--color-chart-2)" />
          </div>
        {/if}
      {/each}
    </section>
  {/if}

  <!-- Regression baseline -->
  <section class="mb-8">
    <h2 class="mb-3 text-sm font-semibold">Regression baseline</h2>
    {#if baseline}
      <div class="rounded-lg border border-border bg-surface p-5">
        <div class="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div>
            <div class="text-xs uppercase tracking-wide text-text-3">
              {baseline.primary_metric_name}
            </div>
            <div class="font-mono text-lg tabular-nums" data-numeric>
              {baseline.primary_metric_value.toFixed(4)}
            </div>
          </div>
          {#if baseline.error_rate !== null}
            <div>
              <div class="text-xs uppercase tracking-wide text-text-3">Error rate</div>
              <div class="font-mono text-lg tabular-nums" data-numeric>
                {baseline.error_rate.toFixed(4)}
              </div>
            </div>
          {/if}
          <div class="min-w-0">
            <div class="text-xs uppercase tracking-wide text-text-3">Anchored to</div>
            <div class="mt-0.5"><CopyableId id={baseline.iteration_id} label="iteration id" /></div>
          </div>
        </div>
        <p class="mt-3 text-xs text-text-3">
          Every run over this dataset is gated against this fixed point. Re-baseline from an
          iteration on its experiment page to raise the bar.
        </p>
      </div>
    {:else}
      <div
        class="rounded-lg border border-dashed border-border bg-surface px-6 py-8 text-center text-sm text-text-3"
      >
        No baseline yet. The first completed run over this dataset auto-anchors one, or set it
        explicitly from an iteration.
      </div>
    {/if}
  </section>

  <!-- Cases -->
  <section>
    <h2 class="mb-3 text-sm font-semibold">
      Cases <span class="text-text-3 font-normal">({ds.cases.length})</span>
    </h2>
    {#if ds.cases.length === 0}
      <p
        class="rounded-lg border border-dashed border-border bg-surface px-6 py-10 text-center text-sm text-text-3"
      >
        No case bodies returned.
      </p>
    {:else}
      <div class="flex flex-col gap-2">
        {#each ds.cases as c (c.id)}
          <CaseRow case_={c} workspaceId={data.workspace.id} />
        {/each}
      </div>
    {/if}
  </section>
</div>

<ConfirmDialog
  open={showFreeze}
  title="Freeze this dataset?"
  message="Freezing recomputes the manifest hash and locks the set — cases can no longer change. This is how you pin a dataset for regression baselining. It can't be undone."
  confirmLabel="Freeze dataset"
  cancelLabel="Keep editable"
  onConfirm={freeze}
  on:close={() => (showFreeze = false)}
/>

<style>
  .split-section {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2rem;
    align-items: center;
    margin-bottom: 2rem;
    padding: 1.1rem 1.3rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
  }
  .split-counts {
    display: flex;
    gap: 1.8rem;
  }
  .split-count {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .split-count-val {
    font-size: var(--text-xl);
    font-weight: 500;
    line-height: 1;
  }
  .split-count-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .split-alloc {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .split-bar {
    display: flex;
    height: 10px;
    border-radius: 999px;
    overflow: hidden;
    background: var(--color-surface-2);
  }
  .split-seg {
    height: 100%;
    transition: width var(--dur-slow) var(--ease-out);
  }
  .split-seg:not(:last-child) {
    border-right: 2px solid var(--color-surface);
  }
  .split-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
  }
  .split-leg {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-xs);
  }
  .split-dot {
    width: 8px;
    height: 8px;
    border-radius: 2px;
  }
  .split-leg-label {
    color: var(--color-text-2);
  }
  .split-leg-val {
    color: var(--color-text-1);
    font-weight: 500;
  }
</style>

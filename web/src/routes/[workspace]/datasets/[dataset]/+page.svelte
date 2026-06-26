<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../../$types';
  import { invalidateAll } from '$app/navigation';
  import { api, ApiError } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import MetricChip from '$lib/components/MetricChip.svelte';
  import BarChart from '$lib/components/charts/BarChart.svelte';
  import CaseRow from '$lib/components/CaseRow.svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';

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
        <span class="frozen-badge">✓ frozen</span>
      {:else}
        <Button variant="secondary" on:click={() => (showFreeze = true)}>Freeze</Button>
      {/if}
    </div>
  </header>

  <!-- Split + counts -->
  <section class="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
    <MetricChip label="Cases" value={ds.case_count} />
    <MetricChip label="Holdout" value={stats?.holdout_count ?? 0} />
    <MetricChip label="Optimization" value={ds.split_allocation.optimization} format="percent" />
    <MetricChip label="Reliability" value={ds.split_allocation.reliability} format="percent" />
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
  .frozen-badge {
    display: inline-block;
    padding: 0.35rem 0.7rem;
    border-radius: var(--radius-md);
    font-size: 12px;
    font-weight: 500;
    color: var(--color-text-1);
    background: color-mix(in srgb, var(--color-accent) 10%, transparent);
    border: 1px solid var(--color-border);
  }
</style>

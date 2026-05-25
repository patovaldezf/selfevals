<script lang="ts">
  import DecisionBadge from '$lib/components/DecisionBadge.svelte';
  import MetricChip from '$lib/components/MetricChip.svelte';
  import Sparkline from '$lib/components/Sparkline.svelte';
  import type { PageData } from './$types';
  import type { LayoutData } from './$types';

  export let data: PageData & LayoutData;

  $: anchorByExp = (() => {
    const map = new Map<string, number[]>();
    for (const p of data.anchor) {
      const arr = map.get(p.experiment_id) ?? [];
      arr.push(p.primary_metric_value);
      map.set(p.experiment_id, arr);
    }
    return map;
  })();

  $: sortedExperiments = [...data.experiments].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  );

  $: anchorAll = data.anchor.map((p) => p.primary_metric_value);

  function fmtDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric'
    });
  }
</script>

<svelte:head>
  <title>{data.workspace.name} · bootstrap</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <header class="mb-10 flex items-end justify-between">
    <div>
      <div class="text-text-3 text-xs font-mono mb-1">
        {data.workspace.slug}
      </div>
      <h1 class="text-3xl font-semibold tracking-tight">
        {data.workspace.name}
      </h1>
      {#if data.workspace.description}
        <p class="text-text-2 mt-1.5">{data.workspace.description}</p>
      {/if}
    </div>
  </header>

  <section class="grid grid-cols-3 gap-4 mb-12">
    <MetricChip
      label="Experiments"
      value={data.workspace.experiment_count}
      format="number"
    />
    <MetricChip
      label="Recent health"
      value={data.workspace.recent_health}
      format="percent"
    />
    <div class="rounded-lg border border-border bg-surface px-4 py-3.5 flex items-center gap-3">
      <div class="flex-1">
        <div class="text-xs uppercase tracking-wide text-text-3 mb-1">
          Anchor pass@1
        </div>
        <div class="font-mono text-sm text-text-2" data-numeric>
          {anchorAll.length > 0
            ? `${anchorAll.length} runs`
            : 'no runs yet'}
        </div>
      </div>
      <Sparkline values={anchorAll} width={100} height={36} />
    </div>
  </section>

  <section>
    <div class="flex items-baseline justify-between mb-4">
      <h2 class="text-lg font-semibold">Recent experiments</h2>
      <span class="text-xs text-text-3">{sortedExperiments.length} total</span>
    </div>

    {#if sortedExperiments.length === 0}
      <div class="rounded-lg border border-border bg-surface px-6 py-12 text-center text-text-2">
        <p class="mb-3">No experiments yet.</p>
        <code class="font-mono text-xs px-2 py-1 rounded bg-surface-2"
          >uv run bootstrap run evals/experiments/your-spec.yaml</code
        >
      </div>
    {:else}
      <div class="border border-border rounded-lg overflow-hidden bg-surface">
        <table class="w-full text-sm">
          <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
            <tr>
              <th class="text-left font-medium px-4 py-2.5">Experiment</th>
              <th class="text-left font-medium px-4 py-2.5">State</th>
              <th class="text-left font-medium px-4 py-2.5">Primary</th>
              <th class="text-right font-medium px-4 py-2.5">Iterations</th>
              <th class="text-right font-medium px-4 py-2.5 pr-6">Trend</th>
              <th class="text-right font-medium px-4 py-2.5 pr-6">Updated</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border">
            {#each sortedExperiments as exp}
              {@const trend = anchorByExp.get(exp.id) ?? []}
              <tr class="hover:bg-surface-2 transition-colors">
                <td class="px-4 py-3">
                  <a
                    href={`/${data.workspace.id}/experiments/${exp.id}`}
                    class="flex flex-col gap-0.5"
                  >
                    <span class="font-medium">{exp.name}</span>
                    <span class="text-text-3 text-xs truncate max-w-md">
                      {exp.goal}
                    </span>
                  </a>
                </td>
                <td class="px-4 py-3 text-text-2 font-mono text-xs">
                  {exp.state}
                </td>
                <td class="px-4 py-3 font-mono text-xs text-text-2">
                  {exp.primary_metric}
                  {exp.primary_target.operator}
                  {exp.primary_target.value}
                </td>
                <td class="px-4 py-3 text-right font-mono" data-numeric>
                  {exp.iteration_count} / {exp.max_iterations}
                </td>
                <td class="px-4 py-3 text-right pr-6">
                  <div class="inline-block">
                    <Sparkline values={trend} width={80} height={20} />
                  </div>
                </td>
                <td class="px-4 py-3 text-right text-text-3 font-mono text-xs pr-6">
                  {fmtDate(exp.updated_at)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <section class="mt-12 grid grid-cols-2 gap-6">
    <div class="rounded-lg border border-border bg-surface px-5 py-5">
      <div class="flex items-baseline justify-between mb-2">
        <h3 class="font-medium">Failure clusters</h3>
        <span class="text-xs text-text-3 font-mono">soon</span>
      </div>
      <p class="text-sm text-text-2 leading-relaxed">
        Clusters of failing traces grouped by failure mode will land here once
        the §J.6 module ships. Reserved real estate so the design doesn't
        retrofit later.
      </p>
    </div>
    <div class="rounded-lg border border-border bg-surface px-5 py-5">
      <div class="flex items-baseline justify-between mb-2">
        <h3 class="font-medium">Datasets</h3>
        <span class="text-xs text-text-3 font-mono">{sortedExperiments.length} active</span>
      </div>
      <p class="text-sm text-text-2 leading-relaxed">
        Browse cases by taxonomy: level, feature, source, ground truth.
        <a class="underline" href={`/${data.workspace.id}/datasets`}>Open ▸</a>
      </p>
    </div>
  </section>
</div>

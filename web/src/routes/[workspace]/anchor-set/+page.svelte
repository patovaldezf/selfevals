<script lang="ts">
  import Sparkline from '$lib/components/Sparkline.svelte';
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';

  export let data: PageData & LayoutData;

  $: byExp = (() => {
    const m = new Map<string, { name: string; values: number[]; dates: string[] }>();
    for (const p of data.points) {
      const cur = m.get(p.experiment_id) ?? {
        name: p.experiment_name,
        values: [],
        dates: []
      };
      cur.values.push(p.primary_metric_value);
      cur.dates.push(p.created_at);
      m.set(p.experiment_id, cur);
    }
    return [...m.entries()];
  })();
</script>

<svelte:head>
  <title>Anchor set · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-5xl mx-auto">
  <header class="mb-8">
    <h1 class="text-2xl font-semibold tracking-tight">Anchor set</h1>
    <p class="text-text-2 mt-1.5 text-sm">
      Primary metric across experiments. Spec §H: this lives behind the
      anchor case set; until it ships, we show per-experiment timelines.
    </p>
  </header>

  {#if byExp.length === 0}
    <div class="rounded-lg border border-border bg-surface px-6 py-12 text-center text-text-2">
      No anchor points yet. Run an experiment to populate.
    </div>
  {:else}
    <div class="space-y-3">
      {#each byExp as [id, rec]}
        <a
          href={`/${data.workspace.id}/experiments/${id}`}
          class="block rounded-lg border border-border bg-surface px-5 py-4 hover:bg-surface-2 transition-colors"
        >
          <div class="flex items-center justify-between">
            <div>
              <div class="font-medium">{rec.name}</div>
              <div class="text-text-3 text-xs font-mono">{id}</div>
            </div>
            <div class="flex items-center gap-6">
              <div class="text-right">
                <div class="text-xs text-text-3">latest</div>
                <div class="font-mono text-sm" data-numeric>
                  {rec.values[rec.values.length - 1]?.toFixed(4) ?? '—'}
                </div>
              </div>
              <Sparkline values={rec.values} width={180} height={36} />
            </div>
          </div>
        </a>
      {/each}
    </div>
  {/if}
</div>

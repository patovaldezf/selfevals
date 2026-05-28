<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';

  export let data: PageData & LayoutData;
</script>

<svelte:head>
  <title>Experiments · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <header class="mb-8 flex items-baseline justify-between">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Experiments</h1>
      <p class="text-text-2 mt-1.5 text-sm">
        All experiments in {data.workspace.name}.
      </p>
    </div>
    <div class="text-xs text-text-3 font-mono" data-numeric>
      {#if data.experimentsHasMore}
        {data.experiments.length} of {data.experimentsTotal}
      {:else}
        {data.experimentsTotal} total
      {/if}
    </div>
  </header>

  <div class="border border-border rounded-lg overflow-hidden bg-surface">
    <table class="w-full text-sm">
      <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
        <tr>
          <th class="text-left px-4 py-2.5 font-medium">Experiment</th>
          <th class="text-left px-4 py-2.5 font-medium">State</th>
          <th class="text-left px-4 py-2.5 font-medium">Proposer</th>
          <th class="text-right px-4 py-2.5 font-medium">Iterations</th>
          <th class="text-right px-4 py-2.5 font-medium pr-6">Updated</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each data.experiments as exp}
          <tr class="hover:bg-surface-2 transition-colors">
            <td class="px-4 py-3">
              <div class="flex flex-col gap-1 items-start">
                <a
                  href={`/${data.workspace.id}/experiments/${exp.id}`}
                  class="font-medium hover:text-text-1"
                >
                  {exp.name}
                </a>
                <CopyableId id={exp.id} label="experiment id" />
              </div>
            </td>
            <td class="px-4 py-3 text-text-2 font-mono text-xs">{exp.state}</td>
            <td class="px-4 py-3 text-text-2 font-mono text-xs">{exp.proposer_strategy}</td>
            <td class="px-4 py-3 text-right font-mono" data-numeric>
              {exp.iteration_count} / {exp.max_iterations}
            </td>
            <td class="px-4 py-3 text-right text-text-3 font-mono text-xs pr-6">
              {new Date(exp.updated_at).toLocaleDateString()}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

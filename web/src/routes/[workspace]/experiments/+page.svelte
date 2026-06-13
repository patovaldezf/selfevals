<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';
  import { goto, invalidateAll } from '$app/navigation';
  import type { RunExperimentResponse } from '$lib/api/client';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import RunExperimentForm from '$lib/components/RunExperimentForm.svelte';

  export let data: PageData & LayoutData;

  let showRun = false;

  async function onLaunched(res: RunExperimentResponse) {
    showRun = false;
    await invalidateAll();
    // Follow the run on its detail page (state climbs queued → running → done).
    goto(`/${data.workspace.id}/experiments/${res.experiment_id}`);
  }
</script>

<svelte:head>
  <title>Experiments · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <header class="mb-8 flex items-end justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Experiments</h1>
      <p class="text-text-2 mt-1.5 text-sm">
        All experiments in {data.workspace.name}.
      </p>
    </div>
    <div class="flex items-center gap-4">
      <div class="text-xs text-text-3 font-mono" data-numeric>
        {#if data.experimentsHasMore}
          {data.experiments.length} of {data.experimentsTotal}
        {:else}
          {data.experimentsTotal} total
        {/if}
      </div>
      <Button variant="primary" on:click={() => (showRun = true)}>Run experiment</Button>
    </div>
  </header>

  {#if data.experiments.length === 0}
    <EmptyState
      icon="◆"
      title="No experiments yet"
      description="Launch your first run from a YAML spec. Inline cases, a grid proposer and a mock sandbox are enough to see the loop end-to-end."
    >
      <svelte:fragment slot="action">
        <Button variant="primary" on:click={() => (showRun = true)}>Run experiment</Button>
      </svelte:fragment>
    </EmptyState>
  {:else}
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
  {/if}
</div>

<Modal open={showRun} title="Run experiment" size="lg" on:close={() => (showRun = false)}>
  <RunExperimentForm
    workspaceId={data.workspace.id}
    datasets={data.datasets}
    on:launched={(e) => onLaunched(e.detail)}
    on:cancel={() => (showRun = false)}
  />
</Modal>

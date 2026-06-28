<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';
  import { goto, invalidateAll } from '$app/navigation';
  import type { RunExperimentResponse } from '$lib/api/client';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import RunExperimentForm from '$lib/components/RunExperimentForm.svelte';
  import { FlaskConical, ArrowRight } from 'lucide-svelte';

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

<div class="page">
  <header class="head">
    <div>
      <h1>Experiments</h1>
      <p class="sub">All experiments in {data.workspace.name}.</p>
    </div>
    <div class="head-right">
      <span class="count mono" data-numeric>
        {#if data.experimentsHasMore}
          {data.experiments.length} of {data.experimentsTotal}
        {:else}
          {data.experimentsTotal} total
        {/if}
      </span>
      <Button variant="brand" on:click={() => (showRun = true)}>Run experiment</Button>
    </div>
  </header>

  {#if data.experiments.length === 0}
    <!-- Onboarding: the first experiment is the path to the whole loop, so spell
         out what a run does instead of a bare "none yet". -->
    <div class="empty">
      <Icon icon={FlaskConical} size={22} />
      <p class="empty-title">No experiments yet</p>
      <p class="empty-sub">
        Launch your first run from a YAML spec. Inline cases, a grid proposer and a mock sandbox are
        enough to see the loop end-to-end — propose, run, grade, decide.
      </p>
      <Button variant="brand" on:click={() => (showRun = true)}>Run experiment</Button>
    </div>
  {:else}
    <div class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th class="l">Experiment</th>
            <th class="l">State</th>
            <th class="l">Proposer</th>
            <th class="r">Iterations</th>
            <th class="r">Updated</th>
            <th class="r"></th>
          </tr>
        </thead>
        <tbody>
          {#each data.experiments as exp (exp.id)}
            <tr on:click={() => goto(`/${data.workspace.id}/experiments/${exp.id}`)}>
              <td>
                <span class="exp-name">{exp.name}</span>
                <span class="exp-goal">{exp.goal}</span>
              </td>
              <td>
                <span class="state">
                  <StatusDot state={exp.state} />
                  <span class="state-label">{exp.state}</span>
                </span>
              </td>
              <td class="mono dim">{exp.proposer_strategy}</td>
              <td class="r mono" data-numeric>{exp.iteration_count} / {exp.max_iterations}</td>
              <td class="r mono dim sm">{new Date(exp.updated_at).toLocaleDateString()}</td>
              <td class="r"><Icon icon={ArrowRight} size={15} class="row-arrow" /></td>
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

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 72rem;
    margin: 0 auto;
  }
  .head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.5rem;
  }
  .head-right {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  h1 {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .sub {
    color: var(--color-text-2);
    margin-top: 0.4rem;
    font-size: var(--text-sm);
  }
  .count {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .table-wrap {
    overflow: hidden;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  thead {
    background: var(--color-surface-2);
  }
  th {
    font-weight: 500;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    padding: 0.6rem 0.9rem;
  }
  th.l {
    text-align: left;
  }
  th.r {
    text-align: right;
  }
  tbody tr {
    border-top: 1px solid var(--color-border);
    cursor: pointer;
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  tbody tr:hover {
    background: var(--color-surface-2);
  }
  tbody tr:hover :global(.row-arrow) {
    transform: translateX(2px);
    color: var(--color-text-1);
  }
  td {
    padding: 0.75rem 0.9rem;
    vertical-align: middle;
  }
  td.r {
    text-align: right;
  }
  td.mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: var(--text-xs);
  }
  .dim {
    color: var(--color-text-3);
  }
  td.sm {
    font-size: var(--text-xs);
  }
  .exp-name {
    display: block;
    font-weight: 500;
    color: var(--color-text-1);
  }
  .exp-goal {
    display: block;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    max-width: 30rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 0.15rem;
  }
  .state {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
  }
  .state-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  :global(.row-arrow) {
    color: var(--color-text-3);
    transition:
      transform var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.6rem;
    padding: 3.5rem 1.5rem;
    text-align: center;
    color: var(--color-text-3);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
  }
  .empty-title {
    font-weight: 600;
    color: var(--color-text-1);
  }
  .empty-sub {
    font-size: var(--text-sm);
    color: var(--color-text-2);
    max-width: 32rem;
    line-height: var(--leading-snug);
  }
</style>

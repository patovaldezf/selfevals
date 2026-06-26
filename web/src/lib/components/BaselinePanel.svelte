<script lang="ts">
  /**
   * Baseline + regression actions for one iteration (loop-closer 2B). Pick a
   * dataset, then either pin this iteration as that dataset's baseline or gate
   * it against the existing baseline. Anchored to a dataset exactly like the
   * CLI. Renders the RegressionResult inline (ok / regression + findings).
   */
  import { api, ApiError, type DatasetSummary, type RegressionResult } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from './ui/Button.svelte';
  import Select from './ui/Select.svelte';
  import ConfirmDialog from './ui/ConfirmDialog.svelte';

  export let workspaceId: string;
  export let iterationId: string;
  export let datasets: DatasetSummary[] = [];

  let datasetId = datasets[0]?.id ?? '';
  let checking = false;
  let result: RegressionResult | null = null;
  let confirmSet = false;

  $: options = datasets.map((d) => ({ value: d.id, label: `${d.name} (${d.case_count})` }));

  async function setBaseline() {
    try {
      await api.setBaseline(workspaceId, datasetId, iterationId);
      toast.success('Baseline set', 'This iteration is now the dataset anchor.');
    } catch (err) {
      toast.error('Set baseline failed', err instanceof ApiError ? err.detail : String(err));
    }
  }

  async function check() {
    if (!datasetId) return;
    checking = true;
    result = null;
    try {
      result = await api.regressionCheck(workspaceId, datasetId, { iteration_id: iterationId });
    } catch (err) {
      toast.error('Regression check failed', err instanceof ApiError ? err.detail : String(err));
    } finally {
      checking = false;
    }
  }
</script>

<div class="flex flex-col gap-3">
  {#if datasets.length === 0}
    <p class="text-xs text-text-3">No datasets in this workspace to baseline against.</p>
  {:else}
    <Select label="Dataset" {options} bind:value={datasetId} />
    <div class="flex gap-2">
      <Button
        size="sm"
        variant="secondary"
        disabled={!datasetId}
        on:click={() => (confirmSet = true)}
      >
        Set as baseline
      </Button>
      <Button size="sm" variant="primary" loading={checking} disabled={!datasetId} on:click={check}>
        Run regression check
      </Button>
    </div>

    {#if result}
      <div
        class="rounded-md border px-3 py-2.5 text-sm"
        class:result-ok={!result.regressed}
        class:result-bad={result.regressed}
      >
        <div class="font-medium">
          {result.regressed ? '✕ Regression detected' : '✓ No regression'}
        </div>
        {#if result.findings.length}
          <ul class="mt-1.5 flex flex-col gap-0.5">
            {#each result.findings as f}
              <li class="flex items-center gap-2 font-mono text-xs">
                <span class:text-danger={f.regressed} class:text-text-3={!f.regressed}>
                  {f.regressed ? 'FAIL' : 'ok'}
                </span>
                <span class="text-text-2">{f.detail}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}
  {/if}
</div>

<ConfirmDialog
  open={confirmSet}
  title="Set this iteration as baseline?"
  message="Re-anchors the dataset's regression baseline to this iteration — the bar future runs are gated against. Overwrites the current baseline."
  confirmLabel="Set baseline"
  onConfirm={setBaseline}
  on:close={() => (confirmSet = false)}
/>

<style>
  .result-ok {
    border-color: color-mix(in srgb, var(--color-success) 35%, transparent);
    background: color-mix(in srgb, var(--color-success) 8%, transparent);
    color: var(--color-success);
  }
  .result-bad {
    border-color: color-mix(in srgb, var(--color-danger) 35%, transparent);
    background: color-mix(in srgb, var(--color-danger) 8%, transparent);
    color: var(--color-danger);
  }
</style>

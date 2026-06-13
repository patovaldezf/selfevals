<script context="module" lang="ts">
  const STARTER = `workspace: REPLACE_WITH_WS_ID

experiment:
  name: my experiment
  goal: describe what you're testing
  mode: handoff
  target:
    primary: { name: pass@1, operator: ">=", value: 0.5 }
  proposer:
    strategy: grid
  search_space:
    model_params:
      level: [0.0, 1.0]
  run:
    sandbox: mock
    max_iterations: 4

dataset:
  cases_inline:
    - id: case_1
      input: { messages: [{ role: user, content: "ping" }] }
      expected: { must_include: ["pong"] }
`;
</script>

<script lang="ts">
  /**
   * Launch-a-run form. Mirrors `RunExperimentRequest`: exactly one of
   * `spec_inline` (a YAML doc parsed to a dict) or `spec_path` (a file on the
   * server). Optional overrides: dataset, max_iterations, reps, persist_traces.
   * On success it emits `launched` with the 202 response so the caller can
   * navigate + follow progress; it also surfaces the `dispatch` mode (a
   * redis-worker run needs a live worker, which is honest UX, not an error).
   */
  import { parse as parseYaml } from 'yaml';
  import { api, ApiError, type RunExperimentResponse, type DatasetSummary } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import { createEventDispatcher } from 'svelte';
  import Button from './ui/Button.svelte';
  import TextField from './ui/TextField.svelte';
  import Select from './ui/Select.svelte';

  export let workspaceId: string;
  /** Datasets available as an override (populated by the caller via GET datasets). */
  export let datasets: DatasetSummary[] = [];

  const dispatch = createEventDispatcher<{ launched: RunExperimentResponse; cancel: void }>();

  type Mode = 'inline' | 'path';
  let mode: Mode = 'inline';
  let specYaml = STARTER;
  let specPath = '';
  let datasetId = '';
  let maxIterations = '';
  let reps = '';
  let persistTraces: 'none' | 'all' | 'failed' = 'failed';

  let submitting = false;
  let formError: string | null = null;

  $: datasetOptions = [
    { value: '', label: 'Use spec default' },
    ...datasets.map((d) => ({ value: d.id, label: `${d.name} (${d.case_count} cases)` }))
  ];

  function buildBody() {
    const body: Record<string, unknown> = {};
    if (mode === 'inline') {
      const parsed = parseYaml(specYaml); // throws on malformed YAML
      if (parsed === null || typeof parsed !== 'object') {
        throw new Error('Spec must be a YAML mapping (object).');
      }
      body.spec_inline = parsed;
    } else {
      if (!specPath.trim()) throw new Error('Provide a spec path on the server.');
      body.spec_path = specPath.trim();
    }
    if (datasetId) body.dataset_id = datasetId;
    if (maxIterations.trim()) body.max_iterations = Number(maxIterations);
    if (reps.trim()) body.reps = Number(reps);
    body.persist_traces = persistTraces;
    return body;
  }

  async function submit() {
    submitting = true;
    formError = null;
    let body: Record<string, unknown>;
    try {
      body = buildBody();
    } catch (err) {
      formError = err instanceof Error ? err.message : String(err);
      submitting = false;
      return;
    }
    try {
      const res = await api.runExperiment(workspaceId, body);
      if (res.dispatch === 'redis-worker') {
        toast.info(
          'Run enqueued on the worker',
          'Needs a live `selfevals worker runs` to make progress.'
        );
      } else {
        toast.success('Run launched', `Experiment ${res.experiment_id}`);
      }
      dispatch('launched', res);
    } catch (err) {
      formError = err instanceof ApiError ? err.detail : String(err);
    } finally {
      submitting = false;
    }
  }
</script>

<form class="flex flex-col gap-4" on:submit|preventDefault={submit}>
  <!-- Mode toggle -->
  <div class="inline-flex rounded-md border border-border bg-surface-2 p-0.5 text-sm">
    <button
      type="button"
      class="rounded px-3 py-1 transition-colors {mode === 'inline'
        ? 'bg-surface font-medium text-text-1 shadow-1'
        : 'text-text-2'}"
      on:click={() => (mode = 'inline')}>YAML spec</button
    >
    <button
      type="button"
      class="rounded px-3 py-1 transition-colors {mode === 'path'
        ? 'bg-surface font-medium text-text-1 shadow-1'
        : 'text-text-2'}"
      on:click={() => (mode = 'path')}>Server path</button
    >
  </div>

  {#if mode === 'inline'}
    <TextField
      label="Experiment spec (YAML)"
      bind:value={specYaml}
      multiline
      rows={14}
      mono
      hint="Same shape as evals/experiments/*.yaml. Inline cases go under dataset.cases_inline."
    />
  {:else}
    <TextField
      label="Spec path on server"
      bind:value={specPath}
      placeholder="evals/experiments/example_pingpong.yaml"
      mono
      hint="A YAML spec already on the API host's disk."
    />
  {/if}

  <div class="grid grid-cols-2 gap-3">
    <Select label="Dataset override" options={datasetOptions} bind:value={datasetId} />
    <Select
      label="Persist traces"
      options={[
        { value: 'failed', label: 'Failed only' },
        { value: 'all', label: 'All' },
        { value: 'none', label: 'None' }
      ]}
      bind:value={persistTraces}
    />
    <TextField
      label="Max iterations"
      bind:value={maxIterations}
      type="number"
      placeholder="spec default"
    />
    <TextField label="Reps" bind:value={reps} type="number" placeholder="1" />
  </div>

  {#if formError}
    <p class="text-sm text-danger">{formError}</p>
  {/if}

  <div class="flex justify-end gap-2 pt-1">
    <Button variant="ghost" on:click={() => dispatch('cancel')}>Cancel</Button>
    <Button variant="primary" type="submit" loading={submitting}>Launch run</Button>
  </div>
</form>

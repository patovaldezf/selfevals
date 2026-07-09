<script context="module" lang="ts">
  // A minimal spec_template: dataset/graders/run/target, no `agent:` (Arena
  // injects that per variant). Deliberately small — a real setup usually
  // grows the dataset via `design-your-dataset`, then pastes the resulting
  // graders/dataset block in here.
  const DEFAULT_SPEC_TEMPLATE = {
    experiment: {
      name: 'arena round',
      goal: 'describe what this arena is comparing',
      mode: 'handoff',
      taxonomy: {
        target_features: ['general'],
        dataset_types: ['capability']
      },
      datasets: { optimization: { id: 'ds_arena' } },
      target: { primary: { name: 'pass@1', operator: '>=', value: 0.0 } },
      frozen: {
        fleet: { id: 'flt_arena' },
        agents: [{ id: 'ag_arena' }],
        datasets: [{ id: 'ds_arena' }]
      },
      proposer: { strategy: 'manual', parameters: { proposals: [{}] } },
      run: { sandbox: 'mock', max_iterations: 1, persist_traces: 'failed' }
    },
    dataset: {
      cases_inline: [
        {
          name: 'example case',
          task_type: 'x',
          input: { messages: [{ role: 'user', content: 'ping' }] },
          taxonomy: {
            level: 'final_response',
            feature: { primary: 'general' },
            source: { type: 'handcrafted' },
            ground_truth: { methods: ['exact_match'] },
            dataset_type: 'capability'
          },
          expected: { must_include: ['pong'] }
        }
      ]
    },
    graders: [{ type: 'deterministic', name: 'rules' }]
  };
</script>

<script lang="ts">
  /**
   * Create an Arena. The non-technical surface: name/goal/repo/command/metric
   * as plain fields, a git branch picker per starting variant (no `git`
   * commands typed by hand), and a dataset picker. `spec_template` (the
   * dataset+graders+run shape shared by every variant's child experiment)
   * stays collapsed behind "Advanced" with a working default — building a
   * full dataset/grader designer here would duplicate `design-your-dataset`;
   * this wizard's job is the arena/variant surface, not dataset authoring.
   */
  import { goto } from '$app/navigation';
  import { api, ApiError, type DatasetSummary, type GitRef } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import TextField from '$lib/components/ui/TextField.svelte';
  import Select from '$lib/components/ui/Select.svelte';
  import type { PageData } from './$types';
  import type { LayoutData } from '../../$types';

  export let data: PageData & LayoutData;

  let name = '';
  let goal = '';
  let repoPath = '';
  let command = '';
  let objectiveMetric = 'pass_rate';
  let datasetId = '';
  let maxRounds = '';
  let maxVariants = '';

  let showAdvanced = false;
  let specTemplateJson = JSON.stringify(DEFAULT_SPEC_TEMPLATE, null, 2);

  // First variant, registered right after the arena is created — a bake-off
  // needs at least one thing to run. Names/refs for more variants are added
  // from the arena detail page once this one is prepared.
  let variantName = 'main';
  let variantRef = '';
  let refs: GitRef[] = [];
  let loadingRefs = false;
  let refsError: string | null = null;

  let submitting = false;
  let formError: string | null = null;

  $: datasetOptions = [
    { value: '', label: 'Use spec_template default (inline cases)' },
    ...data.datasets.map((d: DatasetSummary) => ({
      value: d.id,
      label: `${d.name} (${d.case_count} cases)`
    }))
  ];

  async function loadRefs() {
    if (!repoPath.trim()) return;
    loadingRefs = true;
    refsError = null;
    try {
      const res = await api.gitRefs(data.workspace.id, repoPath.trim());
      refs = res.refs;
      if (refs.length > 0 && !variantRef) variantRef = refs[0].name;
    } catch (err) {
      refs = [];
      refsError = err instanceof ApiError ? err.detail : String(err);
    } finally {
      loadingRefs = false;
    }
  }

  function buildBody() {
    if (!name.trim()) throw new Error('Name is required.');
    if (!goal.trim()) throw new Error('Goal is required.');
    if (!repoPath.trim()) throw new Error('Repo path is required.');
    if (!command.trim()) throw new Error('Agent command is required.');
    if (!variantRef.trim()) throw new Error('Pick a starting branch/ref.');

    let specTemplate: Record<string, unknown>;
    try {
      specTemplate = JSON.parse(specTemplateJson);
    } catch {
      throw new Error('spec_template must be valid JSON.');
    }

    const budget: Record<string, number> = {};
    if (maxRounds.trim()) budget.max_rounds = Number(maxRounds);
    if (maxVariants.trim()) budget.max_variants = Number(maxVariants);

    return {
      name: name.trim(),
      goal: goal.trim(),
      repo_path: repoPath.trim(),
      agent_command: command.trim().split(/\s+/),
      objective_metric: objectiveMetric.trim() || 'pass_rate',
      spec_template: specTemplate,
      dataset_id: datasetId || undefined,
      budget: Object.keys(budget).length > 0 ? budget : undefined
    };
  }

  async function submit() {
    submitting = true;
    formError = null;
    let body: ReturnType<typeof buildBody>;
    try {
      body = buildBody();
    } catch (err) {
      formError = err instanceof Error ? err.message : String(err);
      submitting = false;
      return;
    }
    try {
      const arena = await api.createArena(data.workspace.id, body);
      await api.registerArenaVariant(data.workspace.id, arena.id, {
        name: variantName.trim() || 'main',
        git_ref: variantRef.trim()
      });
      toast.success('Arena created', `${arena.name} — preparing "${variantName}"`);
      goto(`/${data.workspace.id}/arenas/${arena.id}`);
    } catch (err) {
      formError = err instanceof ApiError ? err.detail : String(err);
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head>
  <title>New arena · {data.workspace.name}</title>
</svelte:head>

<div class="page">
  <header class="head">
    <h1>New arena</h1>
    <p class="sub">
      Point at a git repo, name your first variant's branch, and selfevals handles the worktree.
    </p>
  </header>

  <form class="card form" on:submit|preventDefault={submit}>
    <div class="grid grid-cols-2 gap-3">
      <TextField label="Name" bind:value={name} placeholder="tts-bakeoff" required />
      <TextField
        label="Objective metric"
        bind:value={objectiveMetric}
        placeholder="pass_rate"
        required
      />
    </div>
    <TextField
      label="Goal"
      bind:value={goal}
      multiline
      rows={2}
      placeholder="Cheapest TTS provider with acceptable latency on the voice-agent eval set."
      required
    />
    <TextField
      label="Repo path"
      bind:value={repoPath}
      placeholder="/abs/path/to/agent/repo"
      hint="Absolute path to the git repo containing the agent's code, on the API server's filesystem."
      required
      on:input={loadRefs}
    />
    <TextField
      label="Agent command"
      bind:value={command}
      mono
      placeholder="python agent.py"
      hint="Run per case, per variant — its working directory is set to that variant's worktree automatically."
      required
    />
    <Select label="Dataset" options={datasetOptions} bind:value={datasetId} />

    <fieldset class="variant-block">
      <legend>First variant</legend>
      <div class="grid grid-cols-2 gap-3">
        <TextField label="Variant name" bind:value={variantName} placeholder="main" required />
        {#if refs.length > 0}
          <Select
            label="Git branch"
            options={refs.map((r) => ({ value: r.name, label: r.name }))}
            bind:value={variantRef}
          />
        {:else}
          <TextField
            label="Git ref"
            bind:value={variantRef}
            placeholder={loadingRefs ? 'loading branches…' : 'main'}
            hint={refsError ?? 'Branch, tag, or commit already in the repo.'}
            required
          />
        {/if}
      </div>
    </fieldset>

    <div class="grid grid-cols-2 gap-3">
      <TextField
        label="Max rounds (budget)"
        bind:value={maxRounds}
        type="number"
        placeholder="unlimited"
      />
      <TextField
        label="Max variants (budget)"
        bind:value={maxVariants}
        type="number"
        placeholder="unlimited"
      />
    </div>

    <button type="button" class="advanced-toggle" on:click={() => (showAdvanced = !showAdvanced)}>
      {showAdvanced ? '▾' : '▸'} Advanced: dataset + graders (spec_template JSON)
    </button>
    {#if showAdvanced}
      <TextField
        bind:value={specTemplateJson}
        multiline
        rows={16}
        mono
        hint="Same shape as a spec_inline experiment spec, minus `agent:` — Arena injects that per variant. Only edit this if you're wiring a real dataset/graders."
      />
    {/if}

    {#if formError}
      <p class="text-sm text-danger">{formError}</p>
    {/if}

    <div class="flex justify-end gap-2 pt-1">
      <Button variant="ghost" on:click={() => goto(`/${data.workspace.id}/arenas`)}>Cancel</Button>
      <Button variant="primary" type="submit" loading={submitting}>Create arena</Button>
    </div>
  </form>
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 48rem;
    margin: 0 auto;
  }
  .head {
    margin-bottom: 1.5rem;
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
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    padding: 1.5rem;
  }
  .form {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .variant-block {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 1rem;
  }
  .variant-block legend {
    font-size: var(--text-xs);
    font-weight: 500;
    color: var(--color-text-2);
    padding: 0 0.35rem;
  }
  .advanced-toggle {
    text-align: left;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    font-weight: 500;
    padding: 0.25rem 0;
    transition: color var(--dur-fast) var(--ease-out);
  }
  .advanced-toggle:hover {
    color: var(--color-text-1);
  }
</style>

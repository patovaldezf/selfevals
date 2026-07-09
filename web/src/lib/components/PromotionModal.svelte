<!--
  PromotionModal: turn a trace into a permanent regression EvalCase.

  Self-contained state (draft/save flow, dataset picker) and its own fetch —
  the page just tells it which trace and when to open. Custom two-column
  layout (editor + sidebar, max-width 64rem) doesn't fit the shared Modal's
  single-slot contract, so this keeps its own scrim/positioner.
-->
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { X } from 'lucide-svelte';
  import { api, ApiError } from '$lib/api/client';
  import type { AppendDatasetCaseResult, DatasetDetail, DatasetSummary } from '$lib/api/client';

  export let workspaceId: string;
  export let traceId: string;
  export let open: boolean;

  const dispatch = createEventDispatcher<{ close: void }>();

  let loading = false;
  let saving = false;
  let error: string | null = null;
  let draftText = '';
  let regressionDatasets: DatasetSummary[] = [];
  let selectedDatasetId = '';
  let newDatasetName = 'agent regressions';
  let result:
    | AppendDatasetCaseResult
    | { dataset: DatasetDetail; case_id: string; created_new_dataset: boolean }
    | null = null;

  $: if (open) void loadDraft();

  async function loadDraft() {
    loading = true;
    saving = false;
    error = null;
    result = null;
    try {
      const [draft, datasets] = await Promise.all([
        api.draftCaseFromTrace(workspaceId, traceId),
        api.listDatasets(workspaceId, undefined, { dataset_type: 'regression', limit: 100 })
      ]);
      draftText = JSON.stringify(draft.case, null, 2);
      regressionDatasets = datasets.items;
      selectedDatasetId = datasets.items[0]?.id ?? '__new';
    } catch (e) {
      error = e instanceof ApiError ? e.detail : 'Could not draft regression case.';
    } finally {
      loading = false;
    }
  }

  async function save() {
    saving = true;
    error = null;
    try {
      const parsed = JSON.parse(draftText) as Record<string, unknown>;
      if (selectedDatasetId === '__new') {
        const dataset = await api.createDataset(workspaceId, {
          name: newDatasetName.trim() || 'agent regressions',
          dataset_type: 'regression',
          cases: [parsed]
        });
        result = {
          dataset,
          case_id: typeof parsed.id === 'string' ? parsed.id : 'unknown',
          created_new_dataset: true
        };
      } else {
        result = await api.appendDatasetCase(workspaceId, selectedDatasetId, {
          case: parsed,
          create_version_if_frozen: true
        });
      }
    } catch (e) {
      if (e instanceof SyntaxError) {
        error = 'Case JSON is invalid.';
      } else {
        error = e instanceof ApiError ? e.detail : 'Could not save regression case.';
      }
    } finally {
      saving = false;
    }
  }

  $: runCommand =
    result && `selfevals --db ./selfevals.sqlite run <spec.yaml> --dataset ${result.dataset.id}`;
  $: gateCommand =
    result &&
    `selfevals --db ./selfevals.sqlite regression check ${workspaceId} --dataset ${result.dataset.id} --iteration <new_iteration_id>`;
</script>

{#if open}
  <div class="scrim" on:click={() => dispatch('close')} aria-hidden="true"></div>
  <div class="promote-positioner">
    <div class="promote" role="dialog" aria-modal="true" aria-label="Promote to regression case">
      <div class="promote-bar">
        <div>
          <div class="promote-eyebrow">Regression case</div>
          <h2 class="promote-title">Promote this trace</h2>
        </div>
        <button type="button" class="promote-close" on:click={() => dispatch('close')}>
          <Icon icon={X} size={16} />
        </button>
      </div>

      <div class="promote-body">
        <div class="promote-editor">
          {#if loading}
            <div class="promote-loading">Building case draft…</div>
          {:else}
            <label class="promote-field">
              <span class="promote-field-label">Editable EvalCase JSON</span>
              <textarea bind:value={draftText} spellcheck="false"></textarea>
            </label>
          {/if}
        </div>

        <aside class="promote-side">
          <div class="promote-field">
            <label class="promote-field-label" for="dataset-target">Target dataset</label>
            <select id="dataset-target" bind:value={selectedDatasetId} disabled={loading || saving}>
              <option value="__new">Create new regression dataset</option>
              {#each regressionDatasets as ds}
                <option value={ds.id}>{ds.name} · {ds.status} · {ds.case_count} cases</option>
              {/each}
            </select>
          </div>

          {#if selectedDatasetId === '__new'}
            <label class="promote-field">
              <span class="promote-field-label">New dataset name</span>
              <input bind:value={newDatasetName} disabled={saving} />
            </label>
          {/if}

          <div class="promote-note">
            The draft keeps the original expected answer. Review it before saving; this becomes
            regression coverage.
          </div>

          {#if error}
            <div class="promote-err">{error}</div>
          {/if}

          <Button variant="brand" on:click={save} disabled={loading || saving} loading={saving}>
            {saving ? 'Saving…' : 'Save regression case'}
          </Button>

          {#if result}
            <div class="promote-saved">
              <div class="promote-saved-title">Saved</div>
              <div class="promote-saved-row">
                <span>dataset</span>
                <CopyableId id={result.dataset.id} label="dataset id" />
              </div>
              <div class="promote-saved-row">
                <span>case</span>
                <CopyableId id={result.case_id} label="case id" />
              </div>
              {#if result.created_new_dataset}
                <div class="promote-saved-note">Created a new active dataset.</div>
              {/if}
              {#if runCommand}
                <div class="promote-cmd">
                  <div class="promote-cmd-label">Run</div>
                  <pre>{runCommand}</pre>
                </div>
              {/if}
              {#if gateCommand}
                <div class="promote-cmd">
                  <div class="promote-cmd-label">Gate</div>
                  <pre>{gateCommand}</pre>
                </div>
              {/if}
            </div>
          {/if}
        </aside>
      </div>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(10, 10, 10, 0.4);
    z-index: 50;
  }
  .promote-positioner {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem;
    pointer-events: none;
  }
  .promote {
    pointer-events: auto;
    width: 100%;
    max-width: 64rem;
    max-height: 90vh;
    overflow: hidden;
    background: var(--color-bg);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-3);
    display: flex;
    flex-direction: column;
  }
  .promote-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    border-bottom: 1px solid var(--color-border);
    padding: 0.9rem 1.25rem;
  }
  .promote-eyebrow {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .promote-title {
    font-size: var(--text-md);
    font-weight: 600;
  }
  .promote-close {
    color: var(--color-text-3);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .promote-close:hover {
    color: var(--color-text-1);
  }
  .promote-body {
    display: grid;
    grid-template-columns: 1fr 340px;
    min-height: 0;
    flex: 1;
  }
  .promote-editor {
    padding: 1.25rem;
    overflow-y: auto;
  }
  .promote-loading {
    color: var(--color-text-3);
    font-size: var(--text-sm);
    text-align: center;
    padding: 5rem 0;
  }
  .promote-field {
    display: block;
  }
  .promote-field-label {
    display: block;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.5rem;
  }
  .promote-editor textarea {
    width: 100%;
    height: 56vh;
    resize: none;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-md);
    padding: 1rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-1);
    outline: none;
  }
  .promote-editor textarea:focus {
    box-shadow: 0 0 0 2px var(--color-brand-subtle);
    border-color: var(--color-brand);
  }
  .promote-side {
    border-left: 1px solid var(--color-border);
    background: var(--color-surface);
    padding: 1.25rem;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 1.1rem;
  }
  .promote-side select,
  .promote-side input {
    width: 100%;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    border-radius: var(--radius-md);
    padding: 0.5rem;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .promote-note {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    border-radius: var(--radius-md);
    padding: 0.7rem 0.8rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
    line-height: var(--leading-snug);
  }
  .promote-err {
    border: 1px solid var(--color-bad);
    color: var(--color-bad);
    border-radius: var(--radius-md);
    padding: 0.7rem 0.8rem;
    font-size: var(--text-xs);
  }
  .promote-saved {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    border-top: 1px solid var(--color-border);
    padding-top: 1rem;
  }
  .promote-saved-title {
    font-size: var(--text-sm);
    font-weight: 600;
  }
  .promote-saved-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .promote-saved-note {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .promote-cmd-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.25rem;
  }
  .promote-cmd pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    padding: 0.5rem;
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
  }
</style>

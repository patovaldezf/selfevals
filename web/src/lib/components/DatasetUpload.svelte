<script lang="ts">
  /**
   * Upload a `.jsonl` file as a new dataset (multipart). Drag-drop or click.
   * Before uploading we parse the file client-side and show a preview — valid
   * case count, the first few rows, and per-line parse errors — so a malformed
   * file is caught here instead of after a round-trip. On success it emits
   * `uploaded` with the new dataset so the caller can refresh + navigate.
   */
  import { api, ApiError, type DatasetDetail } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import { createEventDispatcher } from 'svelte';
  import Button from './ui/Button.svelte';
  import TextField from './ui/TextField.svelte';
  import Select from './ui/Select.svelte';
  import Icon from './ui/Icon.svelte';
  import { UploadCloud, FileJson, CheckCircle2, AlertTriangle } from 'lucide-svelte';

  export let workspaceId: string;

  const dispatch = createEventDispatcher<{ uploaded: DatasetDetail; cancel: void }>();

  let file: File | null = null;
  let name = '';
  let datasetType = 'capability';
  let description = '';
  let dragOver = false;
  let uploading = false;
  let parsing = false;
  let formError: string | null = null;

  // Client-side validation of the .jsonl before upload. We don't trust it to
  // replace the server's schema check — it just catches the obvious problems
  // (not JSON, empty, line N is broken) and previews what's about to go up.
  type LineError = { line: number; message: string };
  type Preview = {
    total: number;
    valid: number;
    errors: LineError[];
    sample: { line: number; name: string }[];
  } | null;
  let preview: Preview = null;

  async function pickFile(f: File | null) {
    file = f;
    formError = null;
    preview = null;
    if (!f) return;
    if (!name) name = f.name.replace(/\.jsonl?$/i, '');
    await parse(f);
  }

  async function parse(f: File): Promise<void> {
    parsing = true;
    try {
      const text = await f.text();
      const lines = text.split('\n');
      const errors: LineError[] = [];
      const sample: { line: number; name: string }[] = [];
      let valid = 0;
      let total = 0;
      lines.forEach((raw, i) => {
        const trimmed = raw.trim();
        if (!trimmed) return; // blank lines are fine in jsonl
        total += 1;
        try {
          const obj = JSON.parse(trimmed);
          valid += 1;
          if (sample.length < 4) {
            const label =
              (typeof obj.name === 'string' && obj.name) ||
              (typeof obj.id === 'string' && obj.id) ||
              `case ${valid}`;
            sample.push({ line: i + 1, name: label });
          }
        } catch {
          if (errors.length < 6) errors.push({ line: i + 1, message: 'not valid JSON' });
        }
      });
      preview = { total, valid, errors, sample };
    } catch {
      formError = 'Could not read the file.';
    } finally {
      parsing = false;
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragOver = false;
    void pickFile(e.dataTransfer?.files?.[0] ?? null);
  }

  // Block submit when the file has zero valid cases — uploading an empty/broken
  // dataset is never what the user wants.
  $: hasValid = preview !== null && preview.valid > 0;
  $: canSubmit = file !== null && name.trim().length > 0 && hasValid && !uploading && !parsing;

  async function submit() {
    if (!canSubmit || !file) return;
    uploading = true;
    formError = null;
    const form = new FormData();
    form.set('file', file);
    form.set('name', name.trim());
    form.set('dataset_type', datasetType);
    if (description.trim()) form.set('description', description.trim());
    try {
      const ds = await api.uploadDataset(workspaceId, form);
      toast.success('Dataset uploaded', `${ds.name} · ${ds.case_count} cases`);
      dispatch('uploaded', ds);
    } catch (err) {
      formError = err instanceof ApiError ? err.detail : String(err);
    } finally {
      uploading = false;
    }
  }
</script>

<form class="flex flex-col gap-4" on:submit|preventDefault={submit}>
  <label
    class="dropzone"
    class:dropzone-active={dragOver}
    class:has-file={file}
    on:dragover|preventDefault={() => (dragOver = true)}
    on:dragleave={() => (dragOver = false)}
    on:drop={onDrop}
  >
    <input
      type="file"
      accept=".jsonl,.json"
      class="sr-only"
      on:change={(e) => pickFile((e.target as HTMLInputElement).files?.[0] ?? null)}
    />
    {#if file}
      <Icon icon={FileJson} size={22} />
      <span class="file-name mono">{file.name}</span>
      <span class="file-meta">{(file.size / 1024).toFixed(1)} KB · click to replace</span>
    {:else}
      <Icon icon={UploadCloud} size={24} />
      <span class="drop-title">Drop a <code class="mono">.jsonl</code> file or click</span>
      <span class="drop-sub">One JSON eval case per line.</span>
    {/if}
  </label>

  <!-- Pre-flight preview: what parsed, what didn't, before anything is sent. -->
  {#if parsing}
    <div class="preview preview-muted">Parsing…</div>
  {:else if preview}
    <div class="preview" class:preview-bad={preview.valid === 0}>
      <div class="preview-head">
        {#if preview.valid > 0}
          <Icon icon={CheckCircle2} size={15} />
          <span class="preview-stat mono" data-numeric>{preview.valid}</span>
          <span class="preview-label">valid case{preview.valid === 1 ? '' : 's'}</span>
        {:else}
          <Icon icon={AlertTriangle} size={15} />
          <span class="preview-label">No valid cases found</span>
        {/if}
        {#if preview.errors.length > 0}
          <span class="preview-sep">·</span>
          <span class="preview-errcount mono" data-numeric>{preview.errors.length}</span>
          <span class="preview-label">parse error{preview.errors.length === 1 ? '' : 's'}</span>
        {/if}
      </div>
      {#if preview.sample.length > 0}
        <div class="preview-rows">
          {#each preview.sample as s (s.line)}
            <div class="preview-row">
              <span class="preview-row-line mono" data-numeric>L{s.line}</span>
              <span class="preview-row-name">{s.name}</span>
            </div>
          {/each}
          {#if preview.valid > preview.sample.length}
            <div class="preview-more">+{preview.valid - preview.sample.length} more</div>
          {/if}
        </div>
      {/if}
      {#if preview.errors.length > 0}
        <div class="preview-errors">
          {#each preview.errors as e (e.line)}
            <div class="preview-err">
              <span class="preview-row-line mono" data-numeric>L{e.line}</span>
              <span>{e.message}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  <div class="grid grid-cols-2 gap-3">
    <TextField label="Name" bind:value={name} placeholder="my-dataset" required />
    <Select
      label="Type"
      options={[
        { value: 'capability', label: 'Capability' },
        { value: 'golden', label: 'Golden' },
        { value: 'regression', label: 'Regression' },
        { value: 'adversarial', label: 'Adversarial' }
      ]}
      bind:value={datasetType}
    />
  </div>
  <TextField
    label="Description"
    bind:value={description}
    placeholder="What this dataset covers (optional)"
    multiline
    rows={2}
  />

  {#if formError}
    <p class="form-error">{formError}</p>
  {/if}

  <div class="flex justify-end gap-2 pt-1">
    <Button variant="ghost" on:click={() => dispatch('cancel')}>Cancel</Button>
    <Button variant="brand" type="submit" loading={uploading} disabled={!canSubmit}>
      Upload dataset
    </Button>
  </div>
</form>

<style>
  .dropzone {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    text-align: center;
    padding: 2.25rem 1rem;
    border: 1.5px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
    background: var(--color-surface-2);
    cursor: pointer;
    color: var(--color-text-3);
    transition:
      border-color var(--dur-base) var(--ease-out),
      background-color var(--dur-base) var(--ease-out);
  }
  .dropzone:hover,
  .dropzone-active {
    border-color: var(--color-brand);
    background: var(--color-brand-subtle);
  }
  .dropzone.has-file {
    border-style: solid;
    color: var(--color-text-2);
  }
  .file-name {
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .file-meta,
  .drop-sub {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .drop-title {
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .preview {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    padding: 0.7rem 0.85rem;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
  }
  .preview-muted {
    color: var(--color-text-3);
    font-size: var(--text-sm);
  }
  .preview-bad {
    border-color: color-mix(in srgb, var(--color-bad) 35%, var(--color-border));
    background: var(--color-bad-subtle);
  }
  .preview-head {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-sm);
  }
  .preview-bad .preview-head {
    color: var(--color-bad);
  }
  .preview:not(.preview-bad) .preview-head :global(svg) {
    color: var(--color-ok);
  }
  .preview-stat {
    font-weight: 600;
    color: var(--color-text-1);
  }
  .preview-errcount {
    font-weight: 600;
    color: var(--color-bad);
  }
  .preview-label {
    color: var(--color-text-2);
  }
  .preview-sep {
    color: var(--color-text-3);
  }
  .preview-rows,
  .preview-errors {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .preview-row,
  .preview-err {
    display: flex;
    align-items: baseline;
    gap: 0.55rem;
    font-size: var(--text-xs);
  }
  .preview-row-line {
    color: var(--color-text-3);
    flex-shrink: 0;
  }
  .preview-row-name {
    color: var(--color-text-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .preview-err {
    color: var(--color-bad);
  }
  .preview-more {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .form-error {
    font-size: var(--text-sm);
    color: var(--color-danger);
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }
</style>

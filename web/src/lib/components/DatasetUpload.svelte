<script lang="ts">
  /**
   * Upload a `.jsonl` file as a new dataset (multipart). Drag-drop or click.
   * On success it emits `uploaded` with the new dataset so the caller can
   * refresh + navigate. The name defaults to the file stem; type is selectable.
   */
  import { api, ApiError, type DatasetDetail } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import { createEventDispatcher } from 'svelte';
  import Button from './ui/Button.svelte';
  import TextField from './ui/TextField.svelte';
  import Select from './ui/Select.svelte';

  export let workspaceId: string;

  const dispatch = createEventDispatcher<{ uploaded: DatasetDetail; cancel: void }>();

  let file: File | null = null;
  let name = '';
  let datasetType = 'capability';
  let description = '';
  let dragOver = false;
  let uploading = false;
  let formError: string | null = null;

  function pickFile(f: File | null) {
    file = f;
    formError = null;
    if (f && !name) name = f.name.replace(/\.jsonl?$/i, '');
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dragOver = false;
    pickFile(e.dataTransfer?.files?.[0] ?? null);
  }

  $: canSubmit = file !== null && name.trim().length > 0 && !uploading;

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
  <!-- Dropzone -->
  <label
    class="dropzone"
    class:dropzone-active={dragOver}
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
      <span class="font-mono text-sm text-text-1">{file.name}</span>
      <span class="text-xs text-text-3">{(file.size / 1024).toFixed(1)} KB · click to replace</span>
    {:else}
      <span class="text-2xl text-text-3" aria-hidden="true">↑</span>
      <span class="text-sm text-text-1"
        >Drop a <code class="font-mono">.jsonl</code> file or click</span
      >
      <span class="text-xs text-text-3">One JSON eval case per line.</span>
    {/if}
  </label>

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
    <p class="text-sm text-danger">{formError}</p>
  {/if}

  <div class="flex justify-end gap-2 pt-1">
    <Button variant="ghost" on:click={() => dispatch('cancel')}>Cancel</Button>
    <Button variant="primary" type="submit" loading={uploading} disabled={!canSubmit}>
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
    padding: 2.5rem 1rem;
    border: 1.5px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
    background: var(--color-surface-2);
    cursor: pointer;
    transition:
      border-color 0.14s ease-out,
      background-color 0.14s ease-out;
  }
  .dropzone:hover,
  .dropzone-active {
    border-color: var(--color-accent);
    background: color-mix(in srgb, var(--color-accent) 4%, var(--color-surface-2));
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
  }
</style>

<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';
  import { goto, invalidateAll } from '$app/navigation';
  import { page } from '$app/stores';
  import type { DatasetDetail } from '$lib/api/client';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import Select from '$lib/components/ui/Select.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import DatasetUpload from '$lib/components/DatasetUpload.svelte';

  export let data: PageData & LayoutData;

  let showUpload = false;

  const STATUS_TONE: Record<string, string> = {
    draft: 'tone-draft',
    active: 'tone-active',
    frozen: 'tone-frozen',
    archived: 'tone-archived'
  };

  function setFilter(key: 'status' | 'type', value: string) {
    const sp = new URLSearchParams($page.url.searchParams);
    if (value) sp.set(key, value);
    else sp.delete(key);
    goto(`?${sp.toString()}`, { keepFocus: true, noScroll: true });
  }

  async function onUploaded(ds: DatasetDetail) {
    showUpload = false;
    await invalidateAll();
    goto(`/${data.workspace.id}/datasets/${ds.id}`);
  }

  function relativeTime(iso: string): string {
    const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  }
</script>

<svelte:head>
  <title>Datasets · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <header class="mb-7 flex items-end justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Datasets</h1>
      <p class="text-text-2 mt-1.5 text-sm">
        Cases grouped by taxonomy: level, feature, source, ground truth.
      </p>
    </div>
    <Button variant="primary" on:click={() => (showUpload = true)}>Upload dataset</Button>
  </header>

  <!-- Filters -->
  <div class="mb-5 flex items-end gap-3">
    <div class="w-40">
      <Select
        label="Status"
        value={data.status}
        on:change={(e) => setFilter('status', (e.target as HTMLSelectElement).value)}
        options={[
          { value: '', label: 'All statuses' },
          { value: 'draft', label: 'Draft' },
          { value: 'active', label: 'Active' },
          { value: 'frozen', label: 'Frozen' },
          { value: 'archived', label: 'Archived' }
        ]}
      />
    </div>
    <div class="w-40">
      <Select
        label="Type"
        value={data.datasetType}
        on:change={(e) => setFilter('type', (e.target as HTMLSelectElement).value)}
        options={[
          { value: '', label: 'All types' },
          { value: 'capability', label: 'Capability' },
          { value: 'golden', label: 'Golden' },
          { value: 'regression', label: 'Regression' },
          { value: 'adversarial', label: 'Adversarial' }
        ]}
      />
    </div>
  </div>

  {#if data.datasets.length === 0}
    <EmptyState
      icon="▤"
      title="No datasets yet"
      description="Upload a .jsonl of eval cases. Datasets are first-class — persisted, versioned by manifest hash, and reusable across experiments."
    >
      <svelte:fragment slot="action">
        <Button variant="primary" on:click={() => (showUpload = true)}>Upload dataset</Button>
      </svelte:fragment>
    </EmptyState>
  {:else}
    <div class="border border-border rounded-lg overflow-hidden bg-surface">
      <table class="w-full text-sm">
        <thead class="bg-surface-2 text-text-3 text-xs uppercase tracking-wide">
          <tr>
            <th class="text-left px-4 py-2.5 font-medium">Dataset</th>
            <th class="text-left px-4 py-2.5 font-medium">Type</th>
            <th class="text-left px-4 py-2.5 font-medium">Status</th>
            <th class="text-right px-4 py-2.5 font-medium">Cases</th>
            <th class="text-right px-4 py-2.5 font-medium pr-6">Updated</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each data.datasets as ds}
            <tr class="hover:bg-surface-2 transition-colors">
              <td class="px-4 py-3">
                <a
                  href={`/${data.workspace.id}/datasets/${ds.id}`}
                  class="font-medium hover:text-text-1"
                >
                  {ds.name}
                </a>
                {#if ds.description}
                  <div class="text-xs text-text-3 mt-0.5 truncate max-w-md">{ds.description}</div>
                {/if}
              </td>
              <td class="px-4 py-3 text-text-2 font-mono text-xs">{ds.dataset_type}</td>
              <td class="px-4 py-3">
                <span class="status-pill {STATUS_TONE[ds.status] ?? ''}">{ds.status}</span>
              </td>
              <td class="px-4 py-3 text-right font-mono" data-numeric>{ds.case_count}</td>
              <td class="px-4 py-3 text-right text-text-3 font-mono text-xs pr-6">
                {relativeTime(ds.updated_at)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<Modal open={showUpload} title="Upload dataset" size="md" on:close={() => (showUpload = false)}>
  <DatasetUpload
    workspaceId={data.workspace.id}
    on:uploaded={(e) => onUploaded(e.detail)}
    on:cancel={() => (showUpload = false)}
  />
</Modal>

<style>
  .status-pill {
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    text-transform: capitalize;
    background: var(--color-surface-2);
    color: var(--color-text-2);
  }
  .tone-active {
    color: var(--color-success);
    background: color-mix(in srgb, var(--color-success) 10%, transparent);
  }
  .tone-frozen {
    color: var(--color-text-1);
    background: color-mix(in srgb, var(--color-accent) 10%, transparent);
  }
  .tone-archived {
    color: var(--color-text-3);
  }
</style>

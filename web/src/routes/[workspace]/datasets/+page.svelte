<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';
  import { goto, invalidateAll } from '$app/navigation';
  import { page } from '$app/stores';
  import type { DatasetDetail } from '$lib/api/client';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import DatasetUpload from '$lib/components/DatasetUpload.svelte';
  import { Database, ArrowRight, Lock } from 'lucide-svelte';

  export let data: PageData & LayoutData;

  let showUpload = false;

  // Dataset lifecycle → badge tone: active is good (ok), frozen is locked
  // (brand), draft/archived are quiet (neutral). One mapping, no ad-hoc classes.
  const STATUS_TONE: Record<string, 'ok' | 'brand' | 'neutral'> = {
    active: 'ok',
    frozen: 'brand',
    draft: 'neutral',
    archived: 'neutral'
  };

  const STATUS_FILTERS = [
    { value: '', label: 'All' },
    { value: 'draft', label: 'Draft' },
    { value: 'active', label: 'Active' },
    { value: 'frozen', label: 'Frozen' },
    { value: 'archived', label: 'Archived' }
  ];
  const TYPE_FILTERS = [
    { value: '', label: 'All' },
    { value: 'capability', label: 'Capability' },
    { value: 'golden', label: 'Golden' },
    { value: 'regression', label: 'Regression' },
    { value: 'adversarial', label: 'Adversarial' }
  ];

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

<div class="page">
  <header class="head">
    <div>
      <h1>Datasets</h1>
      <p class="sub">Cases grouped by taxonomy: level, feature, source, ground truth.</p>
    </div>
    <Button variant="brand" on:click={() => (showUpload = true)}>Upload dataset</Button>
  </header>

  <div class="filters">
    <div class="seg-group">
      <span class="seg-label">Status</span>
      <div class="seg">
        {#each STATUS_FILTERS as f (f.value)}
          <button
            type="button"
            class="seg-btn"
            class:active={data.status === f.value || (!data.status && f.value === '')}
            on:click={() => setFilter('status', f.value)}>{f.label}</button
          >
        {/each}
      </div>
    </div>
    <div class="seg-group">
      <span class="seg-label">Type</span>
      <div class="seg">
        {#each TYPE_FILTERS as f (f.value)}
          <button
            type="button"
            class="seg-btn"
            class:active={data.datasetType === f.value || (!data.datasetType && f.value === '')}
            on:click={() => setFilter('type', f.value)}>{f.label}</button
          >
        {/each}
      </div>
    </div>
  </div>

  {#if data.datasets.length === 0}
    <div class="empty">
      <Icon icon={Database} size={22} />
      <p class="empty-title">No datasets yet</p>
      <p class="empty-sub">
        Upload a <code class="mono">.jsonl</code> of eval cases. Datasets are first-class — persisted,
        versioned by manifest hash, and reusable across experiments.
      </p>
      <Button variant="brand" on:click={() => (showUpload = true)}>Upload dataset</Button>
    </div>
  {:else}
    <div class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th class="l">Dataset</th>
            <th class="l">Type</th>
            <th class="l">Status</th>
            <th class="r">Cases</th>
            <th class="r">Updated</th>
            <th class="r"></th>
          </tr>
        </thead>
        <tbody>
          {#each data.datasets as ds (ds.id)}
            <tr on:click={() => goto(`/${data.workspace.id}/datasets/${ds.id}`)}>
              <td>
                <span class="ds-name">{ds.name}</span>
                {#if ds.description}
                  <span class="ds-desc">{ds.description}</span>
                {/if}
              </td>
              <td class="mono dim">{ds.dataset_type}</td>
              <td>
                <Badge
                  tone={STATUS_TONE[ds.status] ?? 'neutral'}
                  size="sm"
                  icon={ds.status === 'frozen' ? Lock : undefined}>{ds.status}</Badge
                >
              </td>
              <td class="r mono" data-numeric>{ds.case_count}</td>
              <td class="r mono dim sm">{relativeTime(ds.updated_at)}</td>
              <td class="r"><Icon icon={ArrowRight} size={15} class="row-arrow" /></td>
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
  .filters {
    display: flex;
    gap: 1.5rem;
    margin-bottom: 1.25rem;
  }
  .seg-group {
    display: flex;
    align-items: center;
    gap: 0.55rem;
  }
  .seg-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .seg {
    display: inline-flex;
    padding: 2px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
  }
  .seg-btn {
    padding: 0.25rem 0.6rem;
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    color: var(--color-text-2);
    transition:
      background-color var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .seg-btn:hover {
    color: var(--color-text-1);
  }
  .seg-btn.active {
    background: var(--color-surface);
    color: var(--color-text-1);
    font-weight: 500;
    box-shadow: var(--shadow-1);
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
    padding: 0.7rem 0.9rem;
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
  .ds-name {
    display: block;
    font-weight: 500;
    color: var(--color-text-1);
  }
  .ds-desc {
    display: block;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    max-width: 28rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 0.15rem;
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
    max-width: 30rem;
    line-height: var(--leading-snug);
  }
</style>

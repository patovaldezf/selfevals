<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';
  import { goto, invalidateAll } from '$app/navigation';
  import { page } from '$app/stores';
  import { api, ApiError, type FailureMode } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import TextField from '$lib/components/ui/TextField.svelte';
  import Select from '$lib/components/ui/Select.svelte';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import { CheckCircle2, Pencil, GitMerge, Archive } from 'lucide-svelte';

  export let data: PageData & LayoutData;

  $: wsId = data.workspace.id;

  // Status filter rendered as a segmented control. '' = all.
  const FILTERS = [
    { value: '', label: 'All' },
    { value: 'candidate', label: 'Candidate' },
    { value: 'official', label: 'Official' },
    { value: 'retired', label: 'Retired' }
  ];

  function statusTone(status: string): 'candidate' | 'official' | 'retired' | 'neutral' {
    if (status === 'candidate' || status === 'official' || status === 'retired') return status;
    return 'neutral';
  }

  function setStatus(value: string) {
    const sp = new URLSearchParams($page.url.searchParams);
    if (value) sp.set('status', value);
    else sp.delete('status');
    goto(`?${sp.toString()}`, { keepFocus: true, noScroll: true });
  }

  // --- Action wiring -----------------------------------------------------
  let confirmTarget: { mode: FailureMode; action: 'promote' | 'retire' } | null = null;
  let editTarget: FailureMode | null = null;
  let editTitle = '';
  let editDefinition = '';
  let mergeTarget: FailureMode | null = null;
  let mergeInto = '';

  $: mergeOptions = data.modes
    .filter((m) => m.id !== mergeTarget?.id && m.status !== 'retired')
    .map((m) => ({ value: m.id, label: `${m.slug} (${m.status})` }));

  async function runConfirm() {
    if (!confirmTarget) return;
    const { mode, action } = confirmTarget;
    try {
      if (action === 'promote') await api.promoteFailureMode(wsId, mode.id);
      else await api.retireFailureMode(wsId, mode.id);
      toast.success(action === 'promote' ? 'Promoted to official' : 'Retired', mode.slug);
      await invalidateAll();
    } catch (err) {
      toast.error('Action failed', err instanceof ApiError ? err.detail : String(err));
    }
  }

  function openEdit(m: FailureMode) {
    editTarget = m;
    editTitle = m.title;
    editDefinition = m.definition;
  }

  async function saveEdit() {
    if (!editTarget) return;
    try {
      await api.editFailureMode(wsId, editTarget.id, {
        title: editTitle.trim(),
        definition: editDefinition.trim()
      });
      toast.success('Failure mode updated', editTarget.slug);
      editTarget = null;
      await invalidateAll();
    } catch (err) {
      toast.error('Update failed', err instanceof ApiError ? err.detail : String(err));
    }
  }

  function openMerge(m: FailureMode) {
    mergeTarget = m;
    mergeInto = '';
  }

  async function saveMerge() {
    if (!mergeTarget || !mergeInto) return;
    try {
      await api.mergeFailureMode(wsId, mergeTarget.id, mergeInto);
      toast.success('Merged', `${mergeTarget.slug} → destination; source retired`);
      mergeTarget = null;
      await invalidateAll();
    } catch (err) {
      toast.error('Merge failed', err instanceof ApiError ? err.detail : String(err));
    }
  }
</script>

<svelte:head>
  <title>Failure modes · {data.workspace.name}</title>
</svelte:head>

<div class="page">
  <header class="head">
    <h1>Failure modes</h1>
    <p class="sub">
      The taxonomy of how this workspace's agents fail. Candidates come from error analysis; promote
      the real ones to official — only official modes feed the proposer.
    </p>
  </header>

  <!-- Status filter as a segmented control, not a bare <select>. -->
  <div class="segmented" role="tablist" aria-label="Filter by status">
    {#each FILTERS as f}
      <button
        type="button"
        role="tab"
        aria-selected={data.status === f.value}
        class="seg"
        class:active={data.status === f.value}
        on:click={() => setStatus(f.value)}
      >
        {f.label}
      </button>
    {/each}
  </div>

  {#if data.modes.length === 0}
    <EmptyState
      icon="◇"
      title="No failure modes"
      description="Run error analysis on a failed run to surface candidate failure modes, then promote the ones worth tracking."
    />
  {:else}
    <div class="list">
      {#each data.modes as m (m.id)}
        <div class="mode card">
          <div class="mode-body">
            <div class="mode-main">
              <div class="mode-title">
                <Badge tone={statusTone(m.status)} size="sm">{m.status}</Badge>
                <span class="name">{m.title}</span>
                <span class="slug mono">{m.slug}</span>
              </div>
              <p class="definition">{m.definition}</p>
              <div class="meta">
                <span data-numeric>{m.example_count} examples</span>
                <span>by {m.proposed_by}</span>
                {#if m.superseded_by}<span>→ merged into {m.superseded_by}</span>{/if}
                <CopyableId id={m.id} label="failure mode id" />
              </div>
            </div>
            {#if m.status !== 'retired'}
              <div class="actions">
                {#if m.status === 'candidate'}
                  <Button
                    size="sm"
                    variant="primary"
                    on:click={() => (confirmTarget = { mode: m, action: 'promote' })}
                  >
                    <Icon icon={CheckCircle2} size={14} /> Promote
                  </Button>
                {/if}
                <Button size="sm" variant="ghost" title="Edit" on:click={() => openEdit(m)}>
                  <Icon icon={Pencil} size={14} />
                </Button>
                <Button size="sm" variant="ghost" title="Merge" on:click={() => openMerge(m)}>
                  <Icon icon={GitMerge} size={14} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  title="Retire"
                  on:click={() => (confirmTarget = { mode: m, action: 'retire' })}
                >
                  <Icon icon={Archive} size={14} />
                </Button>
              </div>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<!-- Promote / retire confirm -->
<ConfirmDialog
  open={confirmTarget !== null}
  title={confirmTarget?.action === 'promote' ? 'Promote to official?' : 'Retire this failure mode?'}
  message={confirmTarget?.action === 'promote'
    ? 'Official modes feed the optimization proposer. Promote only modes you trust as real.'
    : 'Retired modes stop appearing in analysis. History is kept; this can be re-promoted later.'}
  confirmLabel={confirmTarget?.action === 'promote' ? 'Promote' : 'Retire'}
  tone={confirmTarget?.action === 'retire' ? 'danger' : 'primary'}
  onConfirm={runConfirm}
  on:close={() => (confirmTarget = null)}
/>

<!-- Edit -->
<Modal open={editTarget !== null} title="Edit failure mode" on:close={() => (editTarget = null)}>
  <form class="flex flex-col gap-4" on:submit|preventDefault={saveEdit}>
    <TextField label="Title" bind:value={editTitle} />
    <TextField
      label="Definition"
      bind:value={editDefinition}
      multiline
      rows={4}
      hint="The testable distinction — what separates this mode from its neighbours."
    />
  </form>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (editTarget = null)}>Cancel</Button>
    <Button variant="primary" on:click={saveEdit}>Save</Button>
  </svelte:fragment>
</Modal>

<!-- Merge -->
<Modal open={mergeTarget !== null} title="Merge failure mode" on:close={() => (mergeTarget = null)}>
  <p class="mb-4 text-sm text-text-2">
    Move <span class="font-mono text-text-1">{mergeTarget?.slug}</span>'s examples into another mode
    and retire it. History is preserved via a back-pointer.
  </p>
  <Select
    label="Merge into"
    placeholder="Pick a destination"
    options={mergeOptions}
    bind:value={mergeInto}
  />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (mergeTarget = null)}>Cancel</Button>
    <Button variant="primary" disabled={!mergeInto} on:click={saveMerge}>Merge</Button>
  </svelte:fragment>
</Modal>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 72rem;
    margin: 0 auto;
  }
  .head {
    margin-bottom: 1.25rem;
  }
  h1 {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .sub {
    color: var(--color-text-2);
    margin-top: 0.4rem;
    font-size: var(--text-sm);
    max-width: 44rem;
  }
  .segmented {
    display: inline-flex;
    gap: 0.15rem;
    padding: 0.2rem;
    margin-bottom: 1.25rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }
  .seg {
    padding: 0.3rem 0.7rem;
    border-radius: var(--radius-sm);
    font-size: var(--text-sm);
    color: var(--color-text-2);
    transition:
      background-color var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .seg:hover {
    color: var(--color-text-1);
  }
  .seg.active {
    background: var(--color-surface-2);
    color: var(--color-text-1);
    font-weight: 500;
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .mode {
    padding: 0.9rem 1.1rem;
    transition: border-color var(--dur-fast) var(--ease-out);
  }
  .mode:hover {
    border-color: var(--color-border-strong);
  }
  .mode-body {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
  }
  .mode-main {
    min-width: 0;
  }
  .mode-title {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .name {
    font-weight: 500;
    color: var(--color-text-1);
  }
  .slug {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .definition {
    margin-top: 0.4rem;
    font-size: var(--text-sm);
    color: var(--color-text-2);
    line-height: var(--leading-snug);
  }
  .meta {
    margin-top: 0.5rem;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.25rem 0.75rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .actions {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    gap: 0.35rem;
  }
</style>

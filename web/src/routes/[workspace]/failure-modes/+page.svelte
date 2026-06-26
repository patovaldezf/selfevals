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
  import EmptyState from '$lib/components/EmptyState.svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';

  export let data: PageData & LayoutData;

  $: wsId = data.workspace.id;

  const STATUS_TONE: Record<string, string> = {
    candidate: 'tone-candidate',
    official: 'tone-official',
    retired: 'tone-retired'
  };

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

<div class="px-12 py-10 max-w-6xl mx-auto">
  <header class="mb-7">
    <h1 class="text-2xl font-semibold tracking-tight">Failure modes</h1>
    <p class="text-text-2 mt-1.5 text-sm">
      The taxonomy of how this workspace's agents fail. Candidates come from error analysis; promote
      the real ones to official — only official modes feed the proposer.
    </p>
  </header>

  <div class="mb-5 w-44">
    <Select
      label="Status"
      value={data.status}
      on:change={(e) => setStatus((e.target as HTMLSelectElement).value)}
      options={[
        { value: '', label: 'All statuses' },
        { value: 'candidate', label: 'Candidate' },
        { value: 'official', label: 'Official' },
        { value: 'retired', label: 'Retired' }
      ]}
    />
  </div>

  {#if data.modes.length === 0}
    <EmptyState
      icon="◇"
      title="No failure modes"
      description="Run error analysis on a failed run to surface candidate failure modes, then promote the ones worth tracking."
    />
  {:else}
    <div class="flex flex-col gap-2">
      {#each data.modes as m (m.id)}
        <div class="rounded-lg border border-border bg-surface px-4 py-3.5">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <span class="status-pill {STATUS_TONE[m.status] ?? ''}">{m.status}</span>
                <span class="font-medium">{m.title}</span>
                <span class="font-mono text-xs text-text-3">{m.slug}</span>
              </div>
              <p class="mt-1.5 text-sm text-text-2">{m.definition}</p>
              <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-3">
                <span>{m.example_count} examples</span>
                <span>by {m.proposed_by}</span>
                {#if m.superseded_by}<span>→ merged into {m.superseded_by}</span>{/if}
                <CopyableId id={m.id} label="failure mode id" />
              </div>
            </div>
            {#if m.status !== 'retired'}
              <div class="flex shrink-0 items-center gap-1.5">
                {#if m.status === 'candidate'}
                  <Button
                    size="sm"
                    variant="primary"
                    on:click={() => (confirmTarget = { mode: m, action: 'promote' })}
                    >Promote</Button
                  >
                {/if}
                <Button size="sm" variant="ghost" on:click={() => openEdit(m)}>Edit</Button>
                <Button size="sm" variant="ghost" on:click={() => openMerge(m)}>Merge</Button>
                <Button
                  size="sm"
                  variant="ghost"
                  on:click={() => (confirmTarget = { mode: m, action: 'retire' })}>Retire</Button
                >
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
  .tone-candidate {
    color: var(--color-warning);
    background: color-mix(in srgb, var(--color-warning) 12%, transparent);
  }
  .tone-official {
    color: var(--color-success);
    background: color-mix(in srgb, var(--color-success) 12%, transparent);
  }
  .tone-retired {
    color: var(--color-text-3);
  }
</style>

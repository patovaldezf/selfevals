<script lang="ts">
  import AppShell from '$lib/components/AppShell.svelte';
  import type { PageData } from './$types';
  import { goto, invalidateAll } from '$app/navigation';
  import { api, ApiError } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import TextField from '$lib/components/ui/TextField.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';

  export let data: PageData;

  let showCreate = false;
  let slug = '';
  let name = '';
  let description = '';
  let saving = false;
  let formError: string | null = null;

  // The slug is the workspace's URL identity — keep it to the lowercase/dash
  // shape the backend validates so the redirect lands.
  $: slugClean = slug.trim().toLowerCase().replace(/\s+/g, '-');
  $: canSubmit = slugClean.length > 0 && !saving;

  function openCreate() {
    slug = '';
    name = '';
    description = '';
    formError = null;
    showCreate = true;
  }

  async function createWorkspace() {
    if (!canSubmit) return;
    saving = true;
    formError = null;
    try {
      const ws = await api.createWorkspace({
        slug: slugClean,
        name: name.trim() || undefined,
        description: description.trim() || undefined
      });
      toast.success('Workspace created', ws.name);
      showCreate = false;
      await invalidateAll();
      goto(`/${ws.slug}`);
    } catch (err) {
      formError = err instanceof ApiError ? err.detail : String(err);
    } finally {
      saving = false;
    }
  }

  function relativeTime(iso: string | null): string {
    if (!iso) return '—';
    const then = new Date(iso).getTime();
    const diff = Date.now() - then;
    const mins = Math.floor(diff / 60_000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  }
</script>

<svelte:head>
  <title>selfevals</title>
</svelte:head>

<AppShell>
  <div class="max-w-5xl mx-auto px-12 py-16">
    <header class="mb-12 flex items-end justify-between gap-4">
      <div>
        <h1 class="text-3xl font-semibold tracking-tight">Workspaces</h1>
        <p class="text-text-2 mt-2 text-sm">
          Every experiment belongs to a workspace. Pick one to dive in.
        </p>
      </div>
      {#if !data.error}
        <Button variant="primary" on:click={openCreate}>New workspace</Button>
      {/if}
    </header>

    {#if data.error}
      <div class="rounded-lg border border-border bg-surface px-6 py-8 text-sm text-text-2">
        <div class="font-medium text-text-1 mb-1">Backend unreachable</div>
        <div class="font-mono text-xs text-text-3 mb-3">{data.error}</div>
        <p>
          Start the API:
          <code class="font-mono text-xs px-1.5 py-0.5 rounded bg-surface-2"
            >uv run selfevals-api</code
          >.
        </p>
      </div>
    {:else if data.workspaces.length === 0}
      <EmptyState
        icon="◳"
        title="No workspaces yet"
        description="A workspace holds your experiments, datasets and failure-mode taxonomy. Create your first one to get started."
      >
        <svelte:fragment slot="action">
          <Button variant="primary" on:click={openCreate}>New workspace</Button>
        </svelte:fragment>
      </EmptyState>
    {:else}
      <ul class="divide-y divide-border border border-border rounded-lg overflow-hidden bg-surface">
        {#each data.workspaces as ws}
          <li>
            <a
              href={`/${ws.id}`}
              class="flex items-center justify-between px-6 py-5 hover:bg-surface-2 transition-colors"
            >
              <div class="min-w-0">
                <div class="flex items-baseline gap-2">
                  <span class="font-medium">{ws.name}</span>
                  <span class="font-mono text-xs text-text-3">{ws.slug}</span>
                </div>
                <div class="text-sm text-text-3 mt-1 truncate">
                  {ws.description ?? `${ws.experiment_count} experiments`}
                </div>
              </div>
              <div class="flex items-center gap-8 text-sm">
                <div class="text-right">
                  <div class="text-text-3 text-xs">last run</div>
                  <div class="font-mono text-text-2">{relativeTime(ws.last_run_at)}</div>
                </div>
                <div class="text-right">
                  <div class="text-text-3 text-xs">experiments</div>
                  <div class="font-mono text-text-1" data-numeric>{ws.experiment_count}</div>
                </div>
                <span class="text-text-3" aria-hidden="true">→</span>
              </div>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</AppShell>

<Modal open={showCreate} title="New workspace" on:close={() => (showCreate = false)}>
  <form class="flex flex-col gap-4" on:submit|preventDefault={createWorkspace}>
    <TextField
      label="Slug"
      bind:value={slug}
      placeholder="my-team"
      required
      hint={slugClean && slugClean !== slug
        ? `Will be saved as “${slugClean}”`
        : 'Lowercase, dashes — used in the URL.'}
    />
    <TextField label="Name" bind:value={name} placeholder="My Team (optional)" />
    <TextField
      label="Description"
      bind:value={description}
      placeholder="What this workspace evaluates (optional)"
      multiline
      rows={3}
    />
    {#if formError}
      <p class="text-sm text-danger">{formError}</p>
    {/if}
  </form>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showCreate = false)}>Cancel</Button>
    <Button variant="primary" loading={saving} disabled={!canSubmit} on:click={createWorkspace}>
      Create workspace
    </Button>
  </svelte:fragment>
</Modal>

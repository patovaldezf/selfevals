<script lang="ts">
  import AppShell from '$lib/components/AppShell.svelte';
  import type { PageData } from './$types';
  import { goto, invalidateAll } from '$app/navigation';
  import { api, ApiError } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import TextField from '$lib/components/ui/TextField.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { LayoutDashboard, ArrowRight, Sparkles, Terminal } from 'lucide-svelte';

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
  <div class="page">
    <header class="head">
      <div>
        <h1>Workspaces</h1>
        <p class="sub">Every experiment belongs to a workspace. Pick one to dive in.</p>
      </div>
      {#if !data.error}
        <Button variant="brand" on:click={openCreate}>New workspace</Button>
      {/if}
    </header>

    {#if data.error}
      <div class="card error-card">
        <div class="error-title">Backend unreachable</div>
        <div class="error-detail mono">{data.error}</div>
        <p class="error-help">
          Start the API:
          <code class="mono">uv run selfevals serve</code>.
        </p>
      </div>
    {:else if data.workspaces.length === 0}
      <!-- First-run onboarding: explain what selfevals is before asking for a
           workspace, so a new user isn't staring at an empty "No X yet". -->
      <div class="onboard">
        <div class="onboard-mark">
          <Icon icon={Sparkles} size={26} />
        </div>
        <h2>Welcome to selfevals</h2>
        <p class="onboard-lede">
          A self-improving eval framework for AI agents. Define a target, point it at a dataset, and
          a proposer loop runs experiments — grading, optimizing and reporting each iteration until
          it beats your bar.
        </p>
        <div class="onboard-steps">
          <div class="onboard-step">
            <span class="onboard-step-n mono">1</span>
            <span>Create a workspace to hold your experiments, datasets and failure modes.</span>
          </div>
          <div class="onboard-step">
            <span class="onboard-step-n mono">2</span>
            <span>Upload a dataset of eval cases, or run a spec with inline cases.</span>
          </div>
          <div class="onboard-step">
            <span class="onboard-step-n mono">3</span>
            <span>Launch a run and watch the metric climb, iteration by iteration.</span>
          </div>
        </div>
        <Button variant="brand" on:click={openCreate}>Create your first workspace</Button>
        <p class="onboard-cli">
          <Icon icon={Terminal} size={13} />
          <span>or from the CLI: <code class="mono">uv run selfevals workspace create</code></span>
        </p>
      </div>
    {:else}
      <div class="card table-wrap">
        <table>
          <thead>
            <tr>
              <th class="l">Workspace</th>
              <th class="l">Description</th>
              <th class="r">Last run</th>
              <th class="r">Experiments</th>
              <th class="r"></th>
            </tr>
          </thead>
          <tbody>
            {#each data.workspaces as ws (ws.id)}
              <tr on:click={() => goto(`/${ws.id}`)}>
                <td>
                  <span class="ws-name">{ws.name}</span>
                  <span class="ws-slug mono">{ws.slug}</span>
                </td>
                <td class="ws-desc dim">{ws.description ?? '—'}</td>
                <td class="r mono dim sm">{relativeTime(ws.last_run_at)}</td>
                <td class="r mono" data-numeric>{ws.experiment_count}</td>
                <td class="r"><Icon icon={ArrowRight} size={15} class="row-arrow" /></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
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
      <p class="form-error">{formError}</p>
    {/if}
  </form>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showCreate = false)}>Cancel</Button>
    <Button variant="brand" loading={saving} disabled={!canSubmit} on:click={createWorkspace}>
      Create workspace
    </Button>
  </svelte:fragment>
</Modal>

<style>
  .page {
    padding: 4rem 3rem;
    max-width: 64rem;
    margin: 0 auto;
  }
  .head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 2.5rem;
  }
  h1 {
    font-size: var(--text-2xl);
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: var(--leading-tight);
  }
  .sub {
    color: var(--color-text-2);
    margin-top: 0.5rem;
    font-size: var(--text-sm);
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
    padding: 0.85rem 0.9rem;
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
  .ws-name {
    font-weight: 500;
    color: var(--color-text-1);
  }
  .ws-slug {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-left: 0.5rem;
  }
  .ws-desc {
    max-width: 24rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  :global(.row-arrow) {
    color: var(--color-text-3);
    transition:
      transform var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }

  /* Onboarding */
  .onboard {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: 0.85rem;
    padding: 3rem 2rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-xl);
    background: var(--color-surface);
  }
  .onboard-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 52px;
    height: 52px;
    border-radius: var(--radius-lg);
    background: var(--color-brand-subtle);
    color: var(--color-brand);
  }
  .onboard h2 {
    font-size: var(--text-lg);
    font-weight: 600;
  }
  .onboard-lede {
    max-width: 34rem;
    color: var(--color-text-2);
    font-size: var(--text-sm);
    line-height: var(--leading-normal);
  }
  .onboard-steps {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    margin: 0.5rem 0 1rem;
    text-align: left;
    width: 100%;
    max-width: 30rem;
  }
  .onboard-step {
    display: flex;
    align-items: flex-start;
    gap: 0.7rem;
    font-size: var(--text-sm);
    color: var(--color-text-2);
  }
  .onboard-step-n {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--color-surface-2);
    color: var(--color-text-1);
    font-size: var(--text-2xs);
    font-weight: 600;
  }
  .onboard-cli {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    margin-top: 0.5rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }

  .error-card {
    padding: 1.5rem 1.75rem;
  }
  .error-title {
    font-weight: 600;
    color: var(--color-text-1);
    margin-bottom: 0.35rem;
  }
  .error-detail {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 0.75rem;
  }
  .error-help {
    font-size: var(--text-sm);
    color: var(--color-text-2);
  }
  code.mono {
    font-family: var(--font-mono);
    font-size: 0.92em;
    padding: 0.1rem 0.4rem;
    border-radius: var(--radius-sm);
    background: var(--color-surface-2);
    color: var(--color-text-2);
  }
  .form-error {
    font-size: var(--text-sm);
    color: var(--color-danger);
  }
</style>

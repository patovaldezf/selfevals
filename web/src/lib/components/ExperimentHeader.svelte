<!--
  ExperimentHeader: breadcrumb + title/goal + state pill + actions.

  Purely presentational; the Cancel button emits `cancel` so the page (which
  owns the ConfirmDialog and the actual cancel request) decides what happens.
-->
<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import { createEventDispatcher } from 'svelte';

  export let workspaceId: string;
  export let name: string;
  export let goal: string;
  export let mode: string;
  export let state: string;
  export let experimentId: string;
  export let isActive: boolean;

  const dispatch = createEventDispatcher<{ cancel: void }>();
</script>

<nav class="text-xs text-text-3 mb-6 flex items-center gap-1.5" aria-label="Breadcrumb">
  <a class="hover:text-text-1" href={`/${workspaceId}`}>workspace</a>
  <span aria-hidden="true">/</span>
  <a class="hover:text-text-1" href={`/${workspaceId}/experiments`}>experiments</a>
  <span aria-hidden="true">/</span>
  <span class="text-text-2">{name}</span>
</nav>

<header class="mb-10 flex items-start justify-between gap-6">
  <div class="min-w-0">
    <div class="text-xs uppercase tracking-wide text-text-3 mb-2">Experiment · {mode}</div>
    <h1 class="text-3xl font-semibold tracking-tight">{name}</h1>
    <p class="text-text-2 mt-2 max-w-2xl">{goal}</p>
    <div class="mt-3">
      <CopyableId id={experimentId} label="experiment id" />
    </div>
  </div>
  <div class="flex shrink-0 items-center gap-3">
    <span class="state-pill">
      <StatusDot {state} />
      <span class="state-pill-label">{state}</span>
    </span>
    <Button
      variant="secondary"
      size="sm"
      href={`/${workspaceId}/experiments/${experimentId}/analyze`}
    >
      Analyze failures
    </Button>
    {#if isActive}
      <Button variant="danger" size="sm" on:click={() => dispatch('cancel')}>Cancel run</Button>
    {/if}
  </div>
</header>

<style>
  .state-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.3rem 0.65rem;
    border-radius: var(--radius-md);
    border: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  .state-pill-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
    text-transform: capitalize;
  }
</style>

<!--
  ExperimentHeader: breadcrumb + title/goal + state pill + actions.

  Purely presentational; the Cancel button emits `cancel` so the page (which
  owns the ConfirmDialog and the actual cancel request) decides what happens.
-->
<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
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

<PageHeader eyebrow={`Experiment · ${mode}`} title={name} subtitle={goal}>
  {#snippet meta()}
    <CopyableId id={experimentId} label="experiment id" />
  {/snippet}
  {#snippet actions()}
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
  {/snippet}
</PageHeader>

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

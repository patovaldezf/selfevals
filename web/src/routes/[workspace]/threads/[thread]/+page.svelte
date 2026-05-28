<script lang="ts">
  import { page } from '$app/stores';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import ThreadTurn from '$lib/components/ThreadTurn.svelte';
  import type { PageData } from './$types';

  export let data: PageData;

  // The [workspace] route param is always present here; assert it so the
  // typed `workspaceId: string` prop on ThreadTurn stays honest.
  $: workspaceId = $page.params.workspace as string;
  $: thread = data.thread;
  $: turnCount = thread.turn_count;
</script>

<svelte:head>
  <title>Thread · selfevals</title>
</svelte:head>

<div class="max-w-3xl mx-auto px-12 py-10">
  <nav class="text-xs text-text-3 mb-6 flex items-center gap-1.5" aria-label="Breadcrumb">
    <a class="hover:text-text-1" href={`/${workspaceId}`}>workspace</a>
    <span aria-hidden="true">/</span>
    <span class="text-text-2">thread</span>
  </nav>

  <header class="mb-8">
    <div class="text-xs uppercase tracking-wide text-text-3 mb-2">Thread</div>
    <div class="flex items-center gap-3">
      <CopyableId id={thread.thread_id} label="thread id" />
      <span class="text-sm text-text-3" data-numeric>
        {turnCount}
        {turnCount === 1 ? 'turn' : 'turns'}
      </span>
    </div>
  </header>

  {#if turnCount === 0}
    <div
      class="rounded-lg border border-border bg-surface px-5 py-8 text-center text-sm text-text-3"
    >
      This thread has no turns.
    </div>
  {:else}
    <div class="flex flex-col gap-4">
      {#each thread.turns as turn (turn.trace_id)}
        <ThreadTurn {turn} {workspaceId} />
      {/each}
    </div>
  {/if}
</div>

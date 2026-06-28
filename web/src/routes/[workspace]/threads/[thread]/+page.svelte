<script lang="ts">
  import { page } from '$app/stores';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import ThreadTurn from '$lib/components/ThreadTurn.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { MessagesSquare } from 'lucide-svelte';
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

<div class="page">
  <nav class="crumb" aria-label="Breadcrumb">
    <a href={`/${workspaceId}`}>workspace</a>
    <span aria-hidden="true">/</span>
    <span class="crumb-here">thread</span>
  </nav>

  <header class="head">
    <div class="head-icon"><Icon icon={MessagesSquare} size={18} /></div>
    <div>
      <h1>Conversation</h1>
      <div class="meta">
        <CopyableId id={thread.thread_id} label="thread id" />
        <span class="turns mono" data-numeric>{turnCount} {turnCount === 1 ? 'turn' : 'turns'}</span
        >
      </div>
    </div>
  </header>

  {#if turnCount === 0}
    <div class="empty">
      <Icon icon={MessagesSquare} size={22} />
      <p class="empty-title">No turns in this thread</p>
    </div>
  {:else}
    <div class="turns-list">
      {#each thread.turns as turn, i (turn.trace_id ?? turn.run_id ?? i)}
        <ThreadTurn {turn} {workspaceId} index={i} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 52rem;
    margin: 0 auto;
  }
  .crumb {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 1.5rem;
  }
  .crumb a:hover {
    color: var(--color-text-1);
  }
  .crumb-here {
    color: var(--color-text-2);
  }
  .head {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 2rem;
  }
  .head-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    color: var(--color-text-2);
    flex-shrink: 0;
  }
  h1 {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-top: 0.4rem;
  }
  .turns {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .turns-list {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.6rem;
    padding: 3rem 1.5rem;
    text-align: center;
    color: var(--color-text-3);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
  }
  .empty-title {
    font-weight: 600;
    color: var(--color-text-1);
  }
</style>

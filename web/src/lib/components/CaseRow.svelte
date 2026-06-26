<script lang="ts">
  /**
   * One eval case, compact. Shows identity (name + id), facets (level, feature,
   * graders, holdout), and a preview of the input — detecting a conversation
   * via a `messages` key. When the case has a persisted trace, links to it.
   * Reused by the dataset detail and (later) the analysis workflow.
   */
  import type { CaseSummary } from '$lib/api/client';
  import CopyableId from './CopyableId.svelte';

  export let case_: CaseSummary;
  export let workspaceId: string;

  function preview(input: Record<string, unknown>): string {
    const messages = input.messages;
    if (Array.isArray(messages) && messages.length) {
      const last = messages[messages.length - 1] as { role?: string; content?: unknown };
      const content =
        typeof last.content === 'string' ? last.content : JSON.stringify(last.content);
      return `${last.role ?? 'msg'}: ${content}`.slice(0, 160);
    }
    return JSON.stringify(input).slice(0, 160);
  }

  $: featureName =
    case_.feature && typeof case_.feature === 'object' && 'name' in case_.feature
      ? (case_.feature as { name: string }).name
      : null;
</script>

<div class="rounded-md border border-border bg-surface px-4 py-3">
  <div class="flex items-start justify-between gap-3">
    <div class="min-w-0">
      <div class="flex items-center gap-2">
        <span class="font-medium text-sm">{case_.name}</span>
        {#if case_.holdout}
          <span class="badge badge-holdout">holdout</span>
        {/if}
        {#if case_.is_conversation}
          <span class="badge">conversation</span>
        {/if}
      </div>
      <p class="mt-1 truncate font-mono text-xs text-text-3">{preview(case_.input)}</p>
    </div>
    {#if case_.latest_trace_id}
      <a
        class="shrink-0 text-xs text-text-2 underline-offset-2 hover:text-text-1 hover:underline"
        href={`/${workspaceId}/traces/${case_.latest_trace_id}`}>trace →</a
      >
    {/if}
  </div>
  <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-3">
    {#if case_.level}<span>level: {case_.level}</span>{/if}
    {#if featureName}<span>feature: {featureName}</span>{/if}
    {#if case_.graders.length}<span>graders: {case_.graders.join(', ')}</span>{/if}
    <CopyableId id={case_.id} label="case id" />
  </div>
</div>

<style>
  .badge {
    display: inline-block;
    padding: 0.05rem 0.4rem;
    border-radius: var(--radius-sm);
    font-size: 10px;
    font-weight: 500;
    background: var(--color-surface-2);
    color: var(--color-text-2);
  }
  .badge-holdout {
    color: var(--color-warning);
    background: color-mix(in srgb, var(--color-warning) 12%, transparent);
  }
</style>

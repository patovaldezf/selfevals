<script lang="ts">
  import type { SpanSummary } from '$lib/api/client';
  import Self from './SpanNode.svelte';

  export let node: SpanSummary;
  export let depth: number;
  export let tree: Map<string | null, SpanSummary[]>;
  export let selected: SpanSummary | null;
  export let setSelected: (s: SpanSummary) => void;

  $: children = tree.get(node.id) ?? [];

  const KIND_COLOR: Record<string, string> = {
    agent_turn: 'var(--color-text-1)',
    llm_call: '#1F1F1F',
    tool_call: '#B45309',
    retrieval: '#6B7280',
    memory_read: '#6B7280',
    memory_write: '#6B7280',
    decision: '#0F7B3E',
    guardrail_check: '#0F7B3E',
    error: '#B91C1C'
  };
</script>

<li>
  <button
    type="button"
    class="w-full flex items-center gap-2 rounded px-2 py-1 text-left hover:bg-surface-2 transition-colors
           {selected?.id === node.id ? 'bg-surface-2' : ''}"
    style:padding-left="{depth * 14 + 8}px"
    on:click={() => setSelected(node)}
  >
    <span
      class="inline-block h-1.5 w-1.5 rounded-full flex-shrink-0"
      style="background: {KIND_COLOR[node.kind] ?? '#6B7280'};"
    ></span>
    <span class="font-mono text-xs truncate flex-1">{node.name}</span>
    <span class="font-mono text-[10px] text-text-3" data-numeric>{node.duration_ms}ms</span>
  </button>
  {#if children.length > 0}
    <ul>
      {#each children as child}
        <Self node={child} depth={depth + 1} {tree} {selected} {setSelected} />
      {/each}
    </ul>
  {/if}
</li>

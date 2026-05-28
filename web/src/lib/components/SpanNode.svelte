<script lang="ts">
  import type { SpanSummary } from '$lib/api/client';
  import { factsFor } from '$lib/spans/facts';
  import { styleForKind } from '$lib/spans/kindStyle';
  import Self from './SpanNode.svelte';

  export let node: SpanSummary;
  export let depth: number;
  export let tree: Map<string | null, SpanSummary[]>;
  export let selected: SpanSummary | null;
  export let setSelected: (s: SpanSummary) => void;

  $: children = tree.get(node.id) ?? [];
  $: style = styleForKind(node.kind);
  $: facts = factsFor(node);

  function fmtDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }
</script>

<li>
  <button
    type="button"
    class="w-full flex items-center gap-2 rounded px-2 py-1 text-left hover:bg-surface-2 transition-colors
           {selected?.id === node.id ? 'bg-surface-2' : ''}"
    style:padding-left="{depth * 14 + 8}px"
    on:click={() => setSelected(node)}
    aria-label="{style.label} span: {node.name}"
  >
    <span
      class="inline-block text-[12px] leading-none flex-shrink-0 w-3 text-center"
      style:color={style.color}
      aria-hidden="true"
      title={style.label}
    >{style.glyph}</span>
    <span class="font-mono text-xs truncate flex-1 min-w-0">{node.name}</span>
    {#each facts as f (f.key)}
      <span
        class="font-mono text-[10px] text-text-3 hidden sm:inline-block whitespace-nowrap"
        title={f.title ?? f.key}
        data-numeric
      >
        {f.value}
      </span>
    {/each}
    <span
      class="font-mono text-[10px] text-text-3 whitespace-nowrap"
      title="duration"
      data-numeric
    >{fmtDuration(node.duration_ms)}</span>
  </button>
  {#if children.length > 0}
    <ul>
      {#each children as child}
        <Self node={child} depth={depth + 1} {tree} {selected} {setSelected} />
      {/each}
    </ul>
  {/if}
</li>

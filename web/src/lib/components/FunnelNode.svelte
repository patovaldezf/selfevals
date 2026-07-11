<script lang="ts">
  import type { FunnelNode } from '$lib/api/client';
  import Pill from './ui/Pill.svelte';

  export let node: FunnelNode;
  // Nesting depth, used only for left indentation. The backend already
  // shaped the tree; we never recompute anything here.
  export let depth = 0;
  /** Clicking a node opens its drill-down (label + failure-mode breakdown).
   *  Threaded through the recursion so every level is selectable. */
  export let onSelect: ((node: FunnelNode) => void) | null = null;
  /** The currently open node, so the row highlights when it's the one shown. */
  export let selectedKey: string | null = null;

  // Mirror the markdown reporter's `_funnel_node_lines` score formatting
  // (4 significant figures, em dash for null) so the CLI and the UI agree.
  function fmtScore(value: number | null): string {
    if (value === null) return '—';
    if (Number.isInteger(value)) return `${value}`;
    return value.toPrecision(4).replace(/\.?0+$/, '');
  }

  // Failure modes ranked by frequency, ties broken by name — same order as
  // the markdown reporter, so the most common failure reads first.
  $: failureModes = Object.entries(node.failure_mode_counts).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  // Labels sorted by name for a stable left-to-right reading order.
  $: labels = Object.entries(node.label_counts).sort((a, b) => a[0].localeCompare(b[0]));
  // Children sorted by key — deterministic, matches the reporter's `sorted()`.
  $: childKeys = Object.keys(node.children).sort();

  const PASS_LABELS = new Set(['pass', 'passed', 'ok', 'success']);
  function isPass(label: string): boolean {
    return PASS_LABELS.has(label.toLowerCase());
  }
</script>

<div class="border-l border-border" style="padding-left: {depth === 0 ? 0 : 16}px">
  <button
    type="button"
    class="node-row {depth > 0 ? 'pl-3' : ''}"
    class:node-sel={selectedKey === node.key}
    on:click={() => onSelect?.(node)}
  >
    <span class="font-mono text-sm text-text-1 truncate">{node.key}</span>

    <div class="flex flex-wrap items-center gap-1.5 min-w-0">
      {#each labels as [label, count]}
        <Pill tone={isPass(label) ? 'positive' : 'neutral'}>{label} · {count}</Pill>
      {/each}
      {#each failureModes as [mode, count]}
        <Pill tone="negative" title="failure mode">{mode} · {count}</Pill>
      {/each}
    </div>

    <div class="ml-auto flex items-baseline gap-3 shrink-0 pl-3">
      <span class="font-mono text-[11px] text-text-3" data-numeric title="contributions · weight">
        n={node.count} · w={fmtScore(node.total_weight)}
      </span>
      <span class="font-mono text-sm tabular-nums text-text-1 w-12 text-right" data-numeric>
        {fmtScore(node.mean_score)}
      </span>
    </div>
  </button>

  {#each childKeys as key}
    <svelte:self node={node.children[key]} depth={depth + 1} {onSelect} {selectedKey} />
  {/each}
</div>

<style>
  .node-row {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
    width: 100%;
    text-align: left;
    padding: 0.375rem 0.4rem;
    border-radius: var(--radius-sm);
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  .node-row:hover {
    background: var(--color-surface-2);
  }
  .node-sel {
    background: var(--color-surface-2);
    box-shadow: inset 2px 0 0 var(--color-brand);
  }
</style>

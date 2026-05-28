<script lang="ts">
  import type { FunnelNode } from '$lib/api/client';

  export let node: FunnelNode;
  // Nesting depth, used only for left indentation. The backend already
  // shaped the tree; we never recompute anything here.
  export let depth = 0;

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
  <div class="flex items-baseline gap-3 py-1.5 {depth > 0 ? 'pl-3' : ''}">
    <span class="font-mono text-sm text-text-1 truncate">{node.key}</span>

    <div class="flex flex-wrap items-center gap-1.5 min-w-0">
      {#each labels as [label, count]}
        <span
          class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
          style:color={isPass(label) ? 'var(--color-success)' : 'var(--color-text-2)'}
          style:background={isPass(label) ? '#E8F5EE' : 'var(--color-surface-2)'}
        >
          {label} · {count}
        </span>
      {/each}
      {#each failureModes as [mode, count]}
        <span
          class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
          style="color: var(--color-danger); background: #FBE9E9;"
          title="failure mode"
        >
          {mode} · {count}
        </span>
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
  </div>

  {#each childKeys as key}
    <svelte:self node={node.children[key]} depth={depth + 1} />
  {/each}
</div>

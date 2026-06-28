<script lang="ts">
  /**
   * Grader funnel as a drill-down of bars rather than a flat tree. Each node is
   * a row: a proportional bar whose width tracks contribution count and whose
   * colour tracks pass-rate against a target (green/amber/red), plus the label
   * tallies and failure modes. Children indent under their parent. Replaces the
   * flat FunnelNode list with something you can read top-down: where in the
   * pipeline cases fall out.
   *
   * The backend owns the rollup; we derive a pass-rate from `label_counts`
   * (pass-ish labels over total) only for colouring — never recompute scores.
   */
  import { thresholdLevel, levelColor, levelSubtle } from '$lib/viz/thresholds';
  import type { FunnelNode } from '$lib/api/client';

  export let node: FunnelNode;
  export let depth = 0;
  /** Pass-rate target used to colour bars. */
  export let target = 0.8;
  /** Peak count across siblings, threaded down so bar widths are comparable. */
  export let peak: number | null = null;

  const PASS_LABELS = new Set(['pass', 'passed', 'ok', 'success', 'true']);

  $: total = Object.values(node.label_counts).reduce((a, b) => a + b, 0);
  $: passCount = Object.entries(node.label_counts)
    .filter(([l]) => PASS_LABELS.has(l.toLowerCase()))
    .reduce((a, [, c]) => a + c, 0);
  $: passRate = total > 0 ? passCount / total : null;
  $: level = thresholdLevel(passRate, { target, direction: 'higher' });

  $: localPeak = peak ?? Math.max(1, node.count);
  $: widthPct = Math.max(4, (node.count / localPeak) * 100);

  $: failureModes = Object.entries(node.failure_mode_counts).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  $: childKeys = Object.keys(node.children).sort();
  $: childPeak = childKeys.length
    ? Math.max(1, ...childKeys.map((k) => node.children[k].count))
    : 1;

  function fmtRate(r: number | null): string {
    return r === null ? '—' : `${(r * 100).toFixed(0)}%`;
  }
</script>

<div
  style="padding-left: {depth === 0 ? 0 : 14}px"
  class={depth > 0 ? 'border-l border-border' : ''}
>
  <div class="py-1.5 {depth > 0 ? 'pl-3' : ''}">
    <div class="mb-1 flex items-baseline justify-between gap-3">
      <span class="truncate font-mono text-sm text-text-1" title={node.key}>{node.key}</span>
      <div class="flex shrink-0 items-baseline gap-2 font-mono text-xs tabular-nums" data-numeric>
        <span class="text-text-3">n={node.count}</span>
        {#if passRate !== null}
          <span style="color: {levelColor(level)};">{fmtRate(passRate)}</span>
        {/if}
      </div>
    </div>

    <div class="h-2 overflow-hidden rounded-full" style="background: var(--color-surface-2);">
      <div
        class="h-full rounded-full"
        style="width: {widthPct}%; background: {passRate !== null
          ? levelColor(level)
          : 'var(--color-chart-2)'};"
      ></div>
    </div>

    {#if failureModes.length > 0}
      <div class="mt-1.5 flex flex-wrap gap-1">
        {#each failureModes.slice(0, 6) as [mode, count]}
          <span
            class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
            style="color: var(--color-bad); background: {levelSubtle('bad')};"
            title="failure mode"
          >
            {mode} · {count}
          </span>
        {/each}
      </div>
    {/if}
  </div>

  {#each childKeys as key (key)}
    <svelte:self node={node.children[key]} depth={depth + 1} {target} peak={childPeak} />
  {/each}
</div>

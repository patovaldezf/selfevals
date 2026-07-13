<!--
  FunnelNodeDrawer — one funnel node opened for drill-down.

  The funnel tree shows every node's rollup at a glance; clicking one opens this
  to read where its score came from: the label distribution (pass/fail/… as
  bars) and the failure modes that bit, ranked. This is the "why is THIS stage
  low" a reader clicks a node to answer, without leaving the funnel.

  Slides in on the F1 spring (ui/Drawer). Pure render of what the backend already
  rolled up. Runes.
-->
<script lang="ts">
  import type { FunnelNode } from '$lib/api/client';
  import Drawer from './ui/Drawer.svelte';
  import BarChart from './charts/BarChart.svelte';
  import Pill from './ui/Pill.svelte';

  let {
    open = false,
    node,
    onClose
  }: {
    open?: boolean;
    node: FunnelNode | null;
    onClose: () => void;
  } = $props();

  const PASS_LABELS = new Set(['pass', 'passed', 'ok', 'success']);

  // Label distribution as bars, pass-ish labels first for a stable read.
  const labelBars = $derived(
    node
      ? Object.entries(node.label_counts)
          .map(([label, value]) => ({ label, value }))
          .sort((a, b) => b.value - a.value)
      : []
  );

  const failureModes = $derived(
    node
      ? Object.entries(node.failure_mode_counts).sort(
          (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
        )
      : []
  );

  function fmtScore(value: number | null | undefined): string {
    if (value == null) return '—';
    if (Number.isInteger(value)) return `${value}`;
    return value.toPrecision(4).replace(/\.?0+$/, '');
  }
</script>

<Drawer {open} size="md" title={undefined} on:close={onClose}>
  {#if node}
    <div class="drill">
      <header class="drill-head">
        <h2 class="key font-mono">{node.key}</h2>
        <div class="stats">
          <span class="stat">
            <span class="stat-val font-mono" data-numeric>{fmtScore(node.mean_score)}</span>
            <span class="stat-label">mean score</span>
          </span>
          <span class="stat">
            <span class="stat-val font-mono" data-numeric>{node.count}</span>
            <span class="stat-label">contributions</span>
          </span>
          <span class="stat">
            <span class="stat-val font-mono" data-numeric>{fmtScore(node.total_weight)}</span>
            <span class="stat-label">weight</span>
          </span>
        </div>
      </header>

      {#if labelBars.length}
        <section class="block">
          <h3 class="block-title">Label distribution</h3>
          <BarChart data={labelBars} />
        </section>
      {/if}

      {#if failureModes.length}
        <section class="block">
          <h3 class="block-title">Failure modes</h3>
          <div class="modes">
            {#each failureModes as [mode, count]}
              <div class="mode-row">
                <Pill tone="negative">{mode}</Pill>
                <span class="mode-count font-mono" data-numeric>{count}</span>
              </div>
            {/each}
          </div>
        </section>
      {/if}

      {#if !labelBars.length && !failureModes.length}
        <p class="empty">This node recorded no labels or failure modes.</p>
      {/if}
    </div>
  {/if}
</Drawer>

<style>
  .drill {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }
  .drill-head {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
  }
  .key {
    font-size: var(--text-md);
    font-weight: 600;
    color: var(--color-text-1);
    overflow-wrap: anywhere;
  }
  .stats {
    display: flex;
    gap: 1.75rem;
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .stat-val {
    font-size: var(--text-lg);
    font-weight: 600;
    color: var(--color-text-1);
    line-height: 1;
  }
  .stat-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .block {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .block-title {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .modes {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .mode-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .mode-count {
    font-size: var(--text-sm);
    color: var(--color-text-2);
  }
  .empty {
    font-size: var(--text-sm);
    color: var(--color-text-3);
  }
</style>

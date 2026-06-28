<script lang="ts">
  import { Sparkline } from '$lib/components/charts';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { thresholdLevel, levelColor, deltaLevel } from '$lib/viz/thresholds';
  import { Anchor, ArrowRight } from 'lucide-svelte';
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';

  export let data: PageData & LayoutData;

  // Group anchor points per experiment: the series is its primary-metric history,
  // the last value is where it currently sits. Anchor points carry no per-target
  // operator, so we colour the latest value against the series' own best as a
  // "trending toward its ceiling" cue rather than a hard pass/fail bar.
  $: byExp = (() => {
    const m = new Map<string, { name: string; values: number[]; dates: string[] }>();
    for (const p of data.points) {
      const cur = m.get(p.experiment_id) ?? { name: p.experiment_name, values: [], dates: [] };
      cur.values.push(p.primary_metric_value);
      cur.dates.push(p.created_at);
      m.set(p.experiment_id, cur);
    }
    return [...m.entries()];
  })();

  function last(values: number[]): number | null {
    return values.length ? values[values.length - 1] : null;
  }
  function trendDelta(values: number[]): number | null {
    if (values.length < 2) return null;
    return values[values.length - 1] - values[values.length - 2];
  }
</script>

<svelte:head>
  <title>Anchor set · {data.workspace.name}</title>
</svelte:head>

<div class="page">
  <header class="head">
    <div class="head-icon"><Icon icon={Anchor} size={18} /></div>
    <div>
      <h1>Anchor set</h1>
      <p class="sub">
        The historical baseline of each experiment's primary metric — the fixed points new runs are
        measured against.
      </p>
    </div>
  </header>

  {#if byExp.length === 0}
    <div class="empty">
      <Icon icon={Anchor} size={22} />
      <p class="empty-title">No anchor points yet</p>
      <p class="empty-sub">
        Run an experiment to start anchoring its primary metric across iterations.
      </p>
    </div>
  {:else}
    <div class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th class="l">Experiment</th>
            <th class="r">Latest</th>
            <th class="r">Δ last</th>
            <th class="r">Trend</th>
            <th class="r"></th>
          </tr>
        </thead>
        <tbody>
          {#each byExp as [id, rec] (id)}
            {@const latest = last(rec.values)}
            {@const peak = rec.values.length ? Math.max(...rec.values) : 0}
            {@const lvl = thresholdLevel(latest, { target: peak, direction: 'higher' })}
            {@const delta = trendDelta(rec.values)}
            <tr on:click={() => (window.location.href = `/${data.workspace.id}/experiments/${id}`)}>
              <td>
                <span class="exp-name">{rec.name}</span>
                <span class="exp-runs mono" data-numeric
                  >{rec.values.length} point{rec.values.length === 1 ? '' : 's'}</span
                >
              </td>
              <td class="r mono" data-numeric>
                {#if latest !== null}
                  <span style="color: {levelColor(lvl)}">{(latest * 100).toFixed(1)}%</span>
                {:else}
                  <span class="dim">—</span>
                {/if}
              </td>
              <td class="r mono sm" data-numeric>
                {#if delta !== null}
                  <span style="color: {levelColor(deltaLevel(delta, 'higher'))}"
                    >{delta > 0 ? '+' : ''}{(delta * 100).toFixed(1)}</span
                  >
                {:else}
                  <span class="dim">—</span>
                {/if}
              </td>
              <td class="r">
                <span class="trend"
                  ><Sparkline values={rec.values} width={96} height={22} endDot /></span
                >
              </td>
              <td class="r"><Icon icon={ArrowRight} size={15} class="row-arrow" /></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 64rem;
    margin: 0 auto;
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
  .sub {
    color: var(--color-text-2);
    margin-top: 0.35rem;
    font-size: var(--text-sm);
    max-width: 42rem;
    line-height: var(--leading-snug);
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .table-wrap {
    overflow: hidden;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  thead {
    background: var(--color-surface-2);
  }
  th {
    font-weight: 500;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    padding: 0.6rem 0.9rem;
  }
  th.l {
    text-align: left;
  }
  th.r {
    text-align: right;
  }
  tbody tr {
    border-top: 1px solid var(--color-border);
    cursor: pointer;
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  tbody tr:hover {
    background: var(--color-surface-2);
  }
  tbody tr:hover :global(.row-arrow) {
    transform: translateX(2px);
    color: var(--color-text-1);
  }
  td {
    padding: 0.7rem 0.9rem;
    vertical-align: middle;
  }
  td.r {
    text-align: right;
  }
  td.mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  td.sm {
    font-size: var(--text-xs);
  }
  .dim {
    color: var(--color-text-3);
  }
  .exp-name {
    display: block;
    font-weight: 500;
    color: var(--color-text-1);
  }
  .exp-runs {
    display: block;
    font-size: var(--text-2xs);
    color: var(--color-text-3);
    margin-top: 0.15rem;
  }
  .trend {
    display: inline-flex;
    color: var(--color-text-3);
  }
  :global(.row-arrow) {
    color: var(--color-text-3);
    transition:
      transform var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.6rem;
    padding: 3.5rem 1.5rem;
    text-align: center;
    color: var(--color-text-3);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
  }
  .empty-title {
    font-weight: 600;
    color: var(--color-text-1);
  }
  .empty-sub {
    font-size: var(--text-sm);
    color: var(--color-text-2);
    max-width: 28rem;
    line-height: var(--leading-snug);
  }
</style>

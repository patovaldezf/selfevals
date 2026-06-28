<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import type { PageData } from './$types';
  import { BarChart, StatRing, DeltaStat } from '$lib/components/charts';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { thresholdLevel, levelColor } from '$lib/viz/thresholds';
  import {
    DollarSign,
    Coins,
    Timer,
    Wrench,
    Gauge,
    AlertTriangle,
    SlidersHorizontal
  } from 'lucide-svelte';

  export let data: PageData;

  // Range presets map to a day count; the picker just rewrites ?from=&to= and
  // lets the server load re-run — no client fetching, consistent with the app.
  const presets = [
    { value: '1', label: '24h' },
    { value: '7', label: '7d' },
    { value: '30', label: '30d' },
    { value: '90', label: '90d' }
  ];

  function rangeDays(): string {
    const ms = new Date(data.to).getTime() - new Date(data.from).getTime();
    const days = Math.round(ms / (24 * 60 * 60 * 1000));
    return presets.some((p) => p.value === String(days)) ? String(days) : '7';
  }

  $: selected = rangeDays();

  function applyPreset(days: string) {
    const to = new Date();
    const from = new Date(to.getTime() - Number(days) * 24 * 60 * 60 * 1000);
    const sp = new URLSearchParams($page.url.searchParams);
    sp.set('from', from.toISOString());
    sp.set('to', to.toISOString());
    goto(`?${sp.toString()}`, { keepFocus: true, noScroll: true });
  }

  // Each metric settled independently; the section shows its own error without
  // blanking the dashboard.
  $: passRate = data.passRate;
  $: failureModes = data.failureModes;
  $: tools = data.tools;
  $: cost = data.cost;
  $: tokens = data.tokens;
  $: latency = data.latency;

  // Top-line rollups. Pass rate is the headline signal; cost/tokens/latency are
  // the operational ones. A workspace-level pass target of 0.7 mirrors the
  // overview's health bar so the colour reads consistently across screens.
  const PASS_TARGET = 0.7;
  $: totalCost =
    cost.ok && cost.value.items.length
      ? cost.value.items.reduce((s, r) => s + r.total_cost_usd, 0)
      : null;
  $: totalTokens =
    tokens.ok && tokens.value.items.length
      ? tokens.value.items.reduce((s, r) => s + r.total_tokens, 0)
      : null;
  $: overallPass =
    passRate.ok && passRate.value.total > 0
      ? passRate.value.items.filter((r) => r.label === 'pass').reduce((s, r) => s + r.count, 0) /
        passRate.value.total
      : null;
  $: latencyP95 =
    latency.ok && latency.value.items.length
      ? (latency.value.items.find((r) => r.metric.includes('total'))?.p95_ms ??
        latency.value.items[0].p95_ms)
      : null;
  // A p95 over ~10s is the kind of thing you'd want flagged; colour it against
  // that so a glance catches a slow window. Lower is better here.
  $: latencyLevel = thresholdLevel(latencyP95, { target: 10000, direction: 'lower' });

  // Pass rate per grader, coloured against the same 0.7 target — a grader bar
  // that dips into amber/red is exactly what you scan this panel for.
  $: passBars = passRate.ok
    ? passRate.value.items
        .filter((r) => r.label === 'pass')
        .map((r) => ({ label: r.grader, value: r.rate, sublabel: `${r.count} cases` }))
    : [];
  // Failure modes ranked by frequency. We colour by share of the worst mode so
  // the biggest offenders read hot without a real "good/bad" target.
  $: failurePeak = failureModes.ok
    ? Math.max(1, ...failureModes.value.items.map((r) => r.count))
    : 1;
  $: failureBars = failureModes.ok
    ? failureModes.value.items.slice(0, 10).map((r) => ({
        label: r.failure_mode,
        value: r.count,
        sublabel: `${(r.rate * 100).toFixed(1)}%`,
        // hottest mode → red, tapering to amber/neutral down the ranking
        color:
          r.count >= failurePeak * 0.66
            ? 'var(--color-bad)'
            : r.count >= failurePeak * 0.33
              ? 'var(--color-warn)'
              : 'var(--color-chart-2)'
      }))
    : [];

  function fmtUsd(v: number): string {
    return `$${v.toFixed(v < 1 ? 4 : 2)}`;
  }
  function fmtInt(v: number): string {
    return v.toLocaleString('en-US');
  }
  function fmtMs(v: number | null): string {
    return v === null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v.toFixed(0)}ms`;
  }
</script>

<svelte:head>
  <title>Metrics · {data.workspace?.name ?? $page.params.workspace}</title>
</svelte:head>

<div class="page">
  <header class="head">
    <div>
      <h1>Metrics</h1>
      <p class="sub">
        Pass rate, failure modes, cost, tokens, tools and latency across the window.
      </p>
    </div>
    <!-- Segmented range control: one tap, no dropdown chrome. -->
    <div class="segmented" role="group" aria-label="Time range">
      {#each presets as p (p.value)}
        <button
          type="button"
          class="seg"
          class:active={selected === p.value}
          on:click={() => applyPreset(p.value)}
        >
          {p.label}
        </button>
      {/each}
    </div>
  </header>

  <!-- Top-line: the four numbers that say "how is this workspace doing". The
       pass-rate ring carries the threshold colour; the rest read large and
       tabular with their own semantic accent. -->
  <section class="stats">
    <div class="card stat-ring">
      <StatRing
        value={overallPass}
        threshold={{ target: PASS_TARGET, direction: 'higher' }}
        label="Pass rate"
        size={92}
      />
      <p class="stat-note">Share of graded cases that passed in the window.</p>
    </div>

    <div class="card stat">
      <span class="stat-icon"><Icon icon={DollarSign} size={15} /></span>
      <DeltaStat label="Total cost" value={totalCost} format="usd" goodWhen="lower" />
    </div>

    <div class="card stat">
      <span class="stat-icon"><Icon icon={Coins} size={15} /></span>
      <DeltaStat label="Total tokens" value={totalTokens} format="count" />
    </div>

    <div class="card stat">
      <span class="stat-icon"><Icon icon={Timer} size={15} /></span>
      <div class="stat-manual">
        <span class="stat-label">Latency p95</span>
        <span class="stat-value mono" style="color: {levelColor(latencyLevel)}" data-numeric>
          {fmtMs(latencyP95)}
        </span>
      </div>
    </div>
  </section>

  <div class="grid">
    <!-- Pass rate by grader -->
    <section class="card panel">
      <div class="panel-head">
        <Icon icon={Gauge} size={15} />
        <h2>Pass rate by grader</h2>
        <span class="panel-target mono">target ≥ {PASS_TARGET}</span>
      </div>
      {#if !passRate.ok}
        <div class="section-error">
          <Icon icon={AlertTriangle} size={15} />
          <span>{passRate.error}</span>
        </div>
      {:else if passBars.length === 0}
        <p class="section-empty">No graded results in range.</p>
      {:else}
        <BarChart
          data={passBars}
          format="percent"
          max={1}
          threshold={{ target: PASS_TARGET, direction: 'higher' }}
        />
      {/if}
    </section>

    <!-- Failure modes -->
    <section class="card panel">
      <div class="panel-head">
        <Icon icon={AlertTriangle} size={15} />
        <h2>Top failure modes</h2>
      </div>
      {#if !failureModes.ok}
        <div class="section-error">
          <Icon icon={AlertTriangle} size={15} />
          <span>{failureModes.error}</span>
        </div>
      {:else if failureBars.length === 0}
        <p class="section-empty">No failure modes recorded.</p>
      {:else}
        <BarChart data={failureBars} format="count" />
      {/if}
    </section>

    <!-- Cost by model -->
    <section class="card panel">
      <div class="panel-head">
        <Icon icon={DollarSign} size={15} />
        <h2>Cost by model</h2>
        {#if totalCost !== null}
          <span class="panel-target mono">{fmtUsd(totalCost)} total</span>
        {/if}
      </div>
      {#if !cost.ok}
        <div class="section-error">
          <Icon icon={AlertTriangle} size={15} />
          <span>{cost.error}</span>
        </div>
      {:else if cost.value.items.length === 0}
        <p class="section-empty">No cost recorded.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th class="l">Model</th>
              <th class="r">Calls</th>
              <th class="r">Total</th>
              <th class="r">Avg</th>
            </tr>
          </thead>
          <tbody>
            {#each cost.value.items as r (r.model + r.provider)}
              <tr>
                <td>
                  <span class="cell-name">{r.model}</span>
                  <span class="cell-sub">{r.provider}</span>
                </td>
                <td class="r mono" data-numeric>{r.call_count}</td>
                <td class="r mono" data-numeric>{fmtUsd(r.total_cost_usd)}</td>
                <td class="r mono dim" data-numeric>{fmtUsd(r.avg_cost_usd)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <!-- Tokens by model -->
    <section class="card panel">
      <div class="panel-head">
        <Icon icon={Coins} size={15} />
        <h2>Tokens by model</h2>
        {#if totalTokens !== null}
          <span class="panel-target mono">{fmtInt(totalTokens)} total</span>
        {/if}
      </div>
      {#if !tokens.ok}
        <div class="section-error">
          <Icon icon={AlertTriangle} size={15} />
          <span>{tokens.error}</span>
        </div>
      {:else if tokens.value.items.length === 0}
        <p class="section-empty">No token usage recorded.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th class="l">Model</th>
              <th class="r">In</th>
              <th class="r">Out</th>
              <th class="r">Total</th>
            </tr>
          </thead>
          <tbody>
            {#each tokens.value.items as r (r.model)}
              <tr>
                <td class="cell-name">{r.model}</td>
                <td class="r mono dim" data-numeric>{fmtInt(r.input_tokens)}</td>
                <td class="r mono dim" data-numeric>{fmtInt(r.output_tokens)}</td>
                <td class="r mono" data-numeric>{fmtInt(r.total_tokens)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <!-- Tools -->
    <section class="card panel">
      <div class="panel-head">
        <Icon icon={Wrench} size={15} />
        <h2>Tool calls</h2>
      </div>
      {#if !tools.ok}
        <div class="section-error">
          <Icon icon={AlertTriangle} size={15} />
          <span>{tools.error}</span>
        </div>
      {:else if tools.value.items.length === 0}
        <p class="section-empty">No tool calls in range.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th class="l">Tool</th>
              <th class="r">Calls</th>
              <th class="r">Errors</th>
              <th class="r">Avg</th>
            </tr>
          </thead>
          <tbody>
            {#each tools.value.items as r (r.tool_name + r.status)}
              <tr>
                <td>
                  <span class="cell-name">{r.tool_name}</span>
                  <span class="cell-sub">{r.status}</span>
                </td>
                <td class="r mono" data-numeric>{r.count}</td>
                <td
                  class="r mono"
                  style={r.error_count > 0 ? 'color: var(--color-bad)' : ''}
                  data-numeric
                >
                  {r.error_count}
                </td>
                <td class="r mono dim" data-numeric>{fmtMs(r.avg_duration_ms)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <!-- Latency -->
    <section class="card panel">
      <div class="panel-head">
        <Icon icon={SlidersHorizontal} size={15} />
        <h2>Latency percentiles</h2>
      </div>
      {#if !latency.ok}
        <div class="section-error">
          <Icon icon={AlertTriangle} size={15} />
          <span>{latency.error}</span>
        </div>
      {:else if latency.value.items.length === 0}
        <p class="section-empty">No latency data in range.</p>
      {:else}
        <table>
          <thead>
            <tr>
              <th class="l">Metric</th>
              <th class="r">p50</th>
              <th class="r">p95</th>
              <th class="r">p99</th>
            </tr>
          </thead>
          <tbody>
            {#each latency.value.items as r (r.metric)}
              <tr>
                <td class="cell-name">{r.metric}</td>
                <td class="r mono dim" data-numeric>{fmtMs(r.p50_ms)}</td>
                <td class="r mono" data-numeric>{fmtMs(r.p95_ms)}</td>
                <td class="r mono dim" data-numeric>{fmtMs(r.p99_ms)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  </div>
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 72rem;
    margin: 0 auto;
  }
  .head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.75rem;
  }
  h1 {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .sub {
    color: var(--color-text-2);
    font-size: var(--text-sm);
    margin-top: 0.4rem;
  }

  /* Segmented control — a single pill that holds the four presets, the active
     one lifted onto the surface with a subtle shadow. */
  .segmented {
    display: inline-flex;
    padding: 0.2rem;
    gap: 0.1rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
  }
  .seg {
    padding: 0.3rem 0.7rem;
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    font-family: var(--font-mono);
    color: var(--color-text-2);
    transition:
      background-color var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .seg:hover {
    color: var(--color-text-1);
  }
  .seg.active {
    background: var(--color-surface);
    color: var(--color-text-1);
    box-shadow: var(--shadow-1);
  }

  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }

  .stats {
    display: grid;
    grid-template-columns: 1.4fr 1fr 1fr 1fr;
    gap: 1rem;
    margin-bottom: 1.75rem;
  }
  .stat-ring {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 1rem 1.2rem;
  }
  .stat-note {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    line-height: var(--leading-snug);
  }
  .stat {
    position: relative;
    display: flex;
    align-items: center;
    padding: 1.1rem 1.2rem;
  }
  .stat-icon {
    position: absolute;
    top: 0.9rem;
    right: 1rem;
    color: var(--color-text-3);
  }
  .stat-manual {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .stat-label {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--color-text-3);
  }
  .stat-value {
    font-size: var(--text-xl);
    font-weight: 500;
    line-height: 1;
  }

  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.25rem;
  }
  .panel {
    padding: 1.1rem 1.2rem 1.2rem;
  }
  .panel-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 1rem;
    color: var(--color-text-2);
  }
  .panel-head h2 {
    flex: 1;
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--color-text-1);
  }
  .panel-target {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }

  .section-empty {
    padding: 1.5rem 0;
    text-align: center;
    font-size: var(--text-sm);
    color: var(--color-text-3);
  }
  .section-error {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 1rem;
    border-radius: var(--radius-md);
    background: var(--color-bad-subtle);
    color: var(--color-bad);
    font-size: var(--text-sm);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  th {
    font-weight: 500;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--color-border);
  }
  th.l {
    text-align: left;
  }
  th.r {
    text-align: right;
  }
  td {
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--color-border);
  }
  tbody tr:last-child td {
    border-bottom: none;
  }
  td.r {
    text-align: right;
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .dim {
    color: var(--color-text-2);
  }
  .cell-name {
    color: var(--color-text-1);
  }
  .cell-sub {
    margin-left: 0.4rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
</style>

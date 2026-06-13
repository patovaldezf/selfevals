<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import type { PageData } from './$types';
  import MetricChip from '$lib/components/MetricChip.svelte';
  import BarChart from '$lib/components/charts/BarChart.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import Select from '$lib/components/ui/Select.svelte';

  export let data: PageData;

  // Range presets map to a day count; "custom" leaves the URL untouched so a
  // hand-set ?from=&to= survives. The picker just rewrites the query and lets
  // the server load re-run — no client fetching, consistent with the app.
  const presets = [
    { value: '1', label: 'Last 24h' },
    { value: '7', label: 'Last 7 days' },
    { value: '30', label: 'Last 30 days' },
    { value: '90', label: 'Last 90 days' }
  ];

  function rangeDays(): string {
    const ms = new Date(data.to).getTime() - new Date(data.from).getTime();
    const days = Math.round(ms / (24 * 60 * 60 * 1000));
    return presets.some((p) => p.value === String(days)) ? String(days) : '7';
  }

  let selected = rangeDays();
  $: selected = rangeDays();

  function applyPreset(days: string) {
    const to = new Date();
    const from = new Date(to.getTime() - Number(days) * 24 * 60 * 60 * 1000);
    const sp = new URLSearchParams($page.url.searchParams);
    sp.set('from', from.toISOString());
    sp.set('to', to.toISOString());
    goto(`?${sp.toString()}`, { keepFocus: true, noScroll: true });
  }

  // Derive renderable views from the settled results. Each metric may have
  // failed independently; the section shows its own error without blanking.
  $: passRate = data.passRate;
  $: failureModes = data.failureModes;
  $: tools = data.tools;
  $: cost = data.cost;
  $: tokens = data.tokens;
  $: latency = data.latency;

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
      ? passRate.value.items
          .filter((r) => r.label === 'pass')
          .reduce((s, r) => s + r.count, 0) / passRate.value.total
      : null;
  $: latencyP95 =
    latency.ok && latency.value.items.length
      ? (latency.value.items.find((r) => r.metric.includes('total'))?.p95_ms ??
        latency.value.items[0].p95_ms)
      : null;

  $: failureBars = failureModes.ok
    ? failureModes.value.items
        .slice(0, 10)
        .map((r) => ({ label: r.failure_mode, value: r.count, sublabel: `${(r.rate * 100).toFixed(1)}%` }))
    : [];
  $: passBars = passRate.ok
    ? passRate.value.items.map((r) => ({
        label: `${r.grader} · ${r.label}`,
        value: r.rate,
        sublabel: `${r.count}`
      }))
    : [];

  function fmtUsd(v: number): string {
    return `$${v.toFixed(v < 1 ? 4 : 2)}`;
  }
  function fmtInt(v: number): string {
    return v.toLocaleString('en-US');
  }
  function fmtMs(v: number | null): string {
    return v === null ? '—' : `${v.toFixed(0)}ms`;
  }
</script>

<svelte:head>
  <title>Metrics · {data.workspace?.name ?? $page.params.workspace}</title>
</svelte:head>

<div class="px-12 py-10 max-w-6xl mx-auto">
  <header class="mb-7 flex items-end justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Metrics</h1>
      <p class="text-text-2 mt-1.5 text-sm">
        Pass rate, failure modes, cost, tokens, tools and latency across the window.
      </p>
    </div>
    <div class="w-44 shrink-0">
      <Select
        options={presets}
        bind:value={selected}
        on:change={(e) => applyPreset((e.target as HTMLSelectElement).value)}
      />
    </div>
  </header>

  <!-- Top-line chips -->
  <div class="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
    <MetricChip label="Pass rate" value={overallPass} format="percent" />
    <MetricChip label="Total cost" value={totalCost === null ? null : fmtUsd(totalCost)} format="plain" />
    <MetricChip
      label="Total tokens"
      value={totalTokens === null ? null : fmtInt(totalTokens)}
      format="plain"
    />
    <MetricChip label="Latency p95" value={latencyP95 === null ? null : fmtMs(latencyP95)} format="plain" />
  </div>

  <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
    <!-- Pass rate by grader -->
    <section class="rounded-lg border border-border bg-surface p-5">
      <h2 class="mb-4 text-sm font-semibold">Pass rate by grader</h2>
      {#if !passRate.ok}
        <p class="text-sm text-danger">{passRate.error}</p>
      {:else if passBars.length === 0}
        <p class="py-4 text-center text-sm text-text-3">No graded results in range.</p>
      {:else}
        <BarChart data={passBars} format="percent" max={1} />
      {/if}
    </section>

    <!-- Failure modes -->
    <section class="rounded-lg border border-border bg-surface p-5">
      <h2 class="mb-4 text-sm font-semibold">Top failure modes</h2>
      {#if !failureModes.ok}
        <p class="text-sm text-danger">{failureModes.error}</p>
      {:else if failureBars.length === 0}
        <p class="py-4 text-center text-sm text-text-3">No failure modes recorded.</p>
      {:else}
        <BarChart data={failureBars} format="count" color="var(--color-chart-3)" />
      {/if}
    </section>

    <!-- Cost by model -->
    <section class="rounded-lg border border-border bg-surface p-5">
      <h2 class="mb-4 text-sm font-semibold">Cost by model</h2>
      {#if !cost.ok}
        <p class="text-sm text-danger">{cost.error}</p>
      {:else if cost.value.items.length === 0}
        <p class="py-4 text-center text-sm text-text-3">No cost recorded.</p>
      {:else}
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-text-3">
              <th class="pb-2 font-medium">Model</th>
              <th class="pb-2 text-right font-medium">Calls</th>
              <th class="pb-2 text-right font-medium">Total</th>
              <th class="pb-2 text-right font-medium">Avg</th>
            </tr>
          </thead>
          <tbody>
            {#each cost.value.items as r}
              <tr class="border-b border-border last:border-0">
                <td class="py-2">
                  <span class="text-text-1">{r.model}</span>
                  <span class="ml-1 text-xs text-text-3">{r.provider}</span>
                </td>
                <td class="py-2 text-right font-mono tabular-nums" data-numeric>{r.call_count}</td>
                <td class="py-2 text-right font-mono tabular-nums" data-numeric>{fmtUsd(r.total_cost_usd)}</td>
                <td class="py-2 text-right font-mono tabular-nums text-text-2" data-numeric>{fmtUsd(r.avg_cost_usd)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <!-- Tokens by model -->
    <section class="rounded-lg border border-border bg-surface p-5">
      <h2 class="mb-4 text-sm font-semibold">Tokens by model</h2>
      {#if !tokens.ok}
        <p class="text-sm text-danger">{tokens.error}</p>
      {:else if tokens.value.items.length === 0}
        <p class="py-4 text-center text-sm text-text-3">No token usage recorded.</p>
      {:else}
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-text-3">
              <th class="pb-2 font-medium">Model</th>
              <th class="pb-2 text-right font-medium">In</th>
              <th class="pb-2 text-right font-medium">Out</th>
              <th class="pb-2 text-right font-medium">Total</th>
            </tr>
          </thead>
          <tbody>
            {#each tokens.value.items as r}
              <tr class="border-b border-border last:border-0">
                <td class="py-2 text-text-1">{r.model}</td>
                <td class="py-2 text-right font-mono tabular-nums text-text-2" data-numeric>{fmtInt(r.input_tokens)}</td>
                <td class="py-2 text-right font-mono tabular-nums text-text-2" data-numeric>{fmtInt(r.output_tokens)}</td>
                <td class="py-2 text-right font-mono tabular-nums" data-numeric>{fmtInt(r.total_tokens)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <!-- Tools -->
    <section class="rounded-lg border border-border bg-surface p-5">
      <h2 class="mb-4 text-sm font-semibold">Tool calls</h2>
      {#if !tools.ok}
        <p class="text-sm text-danger">{tools.error}</p>
      {:else if tools.value.items.length === 0}
        <p class="py-4 text-center text-sm text-text-3">No tool calls in range.</p>
      {:else}
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-text-3">
              <th class="pb-2 font-medium">Tool</th>
              <th class="pb-2 text-right font-medium">Calls</th>
              <th class="pb-2 text-right font-medium">Errors</th>
              <th class="pb-2 text-right font-medium">Avg ms</th>
            </tr>
          </thead>
          <tbody>
            {#each tools.value.items as r}
              <tr class="border-b border-border last:border-0">
                <td class="py-2">
                  <span class="text-text-1">{r.tool_name}</span>
                  <span class="ml-1 text-xs text-text-3">{r.status}</span>
                </td>
                <td class="py-2 text-right font-mono tabular-nums" data-numeric>{r.count}</td>
                <td class="py-2 text-right font-mono tabular-nums" class:text-danger={r.error_count > 0} data-numeric>{r.error_count}</td>
                <td class="py-2 text-right font-mono tabular-nums text-text-2" data-numeric>{fmtMs(r.avg_duration_ms)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    <!-- Latency -->
    <section class="rounded-lg border border-border bg-surface p-5">
      <h2 class="mb-4 text-sm font-semibold">Latency percentiles</h2>
      {#if !latency.ok}
        <p class="text-sm text-danger">{latency.error}</p>
      {:else if latency.value.items.length === 0}
        <p class="py-4 text-center text-sm text-text-3">No latency data in range.</p>
      {:else}
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-left text-xs uppercase tracking-wide text-text-3">
              <th class="pb-2 font-medium">Metric</th>
              <th class="pb-2 text-right font-medium">p50</th>
              <th class="pb-2 text-right font-medium">p95</th>
              <th class="pb-2 text-right font-medium">p99</th>
            </tr>
          </thead>
          <tbody>
            {#each latency.value.items as r}
              <tr class="border-b border-border last:border-0">
                <td class="py-2 text-text-1">{r.metric}</td>
                <td class="py-2 text-right font-mono tabular-nums text-text-2" data-numeric>{fmtMs(r.p50_ms)}</td>
                <td class="py-2 text-right font-mono tabular-nums" data-numeric>{fmtMs(r.p95_ms)}</td>
                <td class="py-2 text-right font-mono tabular-nums text-text-2" data-numeric>{fmtMs(r.p99_ms)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>
  </div>
</div>

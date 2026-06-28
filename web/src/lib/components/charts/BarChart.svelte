<script lang="ts">
  /**
   * Horizontal bar chart for rankings (failure modes, pass-rate by grader,
   * dataset facets). No chart dep — CSS bars. Bars are sorted by the caller; we
   * render the given order.
   *
   * Threshold-aware: pass a `threshold` and each bar colours itself ok/amber/red
   * against the target (e.g. pass-rate per grader vs a goal). Without one, bars
   * use the quiet `color` token. Hovering a bar lifts its value. Bar widths grow
   * in on mount via a CSS width transition.
   *
   * Back-compatible with the previous BarChart: `data`, `format`, `max`, `color`
   * keep their old meaning.
   */
  import { onMount } from 'svelte';
  import { thresholdLevel, levelColor, type ThresholdDirection } from '$lib/viz/thresholds';
  import type { Bar } from './types';

  export let data: Bar[] = [];
  export let format: 'count' | 'percent' = 'count';
  export let max: number | null = null;
  /** Default bar fill when no per-bar color and no threshold applies. */
  export let color = 'var(--color-chart-1)';
  /** When set, each bar is coloured by how its value sits against the target. */
  export let threshold: { target: number; direction?: ThresholdDirection } | null = null;

  $: peak = max ?? Math.max(1, ...data.map((d) => d.value));

  function fmt(v: number): string {
    if (format === 'percent') return `${(v * 100).toFixed(1)}%`;
    return Number.isInteger(v) ? `${v}` : v.toFixed(2);
  }

  function barColor(d: Bar): string {
    if (d.color) return d.color;
    if (threshold) return levelColor(thresholdLevel(d.value, threshold));
    return color;
  }

  let mounted = false;
  onMount(() => {
    mounted = true;
  });
</script>

{#if data.length === 0}
  <p class="py-4 text-center text-sm text-text-3">No data in range.</p>
{:else}
  <div class="flex flex-col gap-2">
    {#each data as d (d.label)}
      <div class="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
        <div class="min-w-0">
          <div class="mb-1 flex items-baseline justify-between gap-2">
            <span class="truncate text-sm text-text-1" title={d.label}>{d.label}</span>
            {#if d.sublabel}
              <span class="shrink-0 text-xs text-text-3">{d.sublabel}</span>
            {/if}
          </div>
          <div class="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div
              class="h-full rounded-full"
              style="width: {mounted
                ? Math.max(2, (d.value / peak) * 100)
                : 0}%; background: {barColor(d)}; transition: width 0.5s var(--ease-out);"
            ></div>
          </div>
        </div>
        <span class="font-mono text-sm tabular-nums text-text-1" data-numeric>{fmt(d.value)}</span>
      </div>
    {/each}
  </div>
{/if}

<script lang="ts">
  /**
   * Horizontal bar chart for rankings (failure modes, pass-rate by grader,
   * dataset facets). No chart dep — CSS bars, same grayscale discipline as
   * Sparkline. Bars are sorted by the caller; we just render the given order.
   * `format` controls the trailing value label (count / percent).
   */
  export let data: { label: string; value: number; sublabel?: string }[] = [];
  export let format: 'count' | 'percent' = 'count';
  export let max: number | null = null;
  /** Color of the bar fill. Defaults to a quiet chart token. */
  export let color = 'var(--color-chart-1)';

  $: peak = max ?? Math.max(1, ...data.map((d) => d.value));

  function fmt(v: number): string {
    if (format === 'percent') return `${(v * 100).toFixed(1)}%`;
    return Number.isInteger(v) ? `${v}` : v.toFixed(2);
  }
</script>

{#if data.length === 0}
  <p class="py-4 text-center text-sm text-text-3">No data in range.</p>
{:else}
  <div class="flex flex-col gap-2">
    {#each data as d}
      <div class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
        <div class="min-w-0">
          <div class="mb-1 flex items-baseline justify-between gap-2">
            <span class="truncate text-sm text-text-1" title={d.label}>{d.label}</span>
            {#if d.sublabel}
              <span class="shrink-0 text-xs text-text-3">{d.sublabel}</span>
            {/if}
          </div>
          <div class="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div
              class="h-full rounded-full transition-[width] duration-500 ease-out"
              style="width: {Math.max(2, (d.value / peak) * 100)}%; background: {color};"
            ></div>
          </div>
        </div>
        <span class="font-mono text-sm tabular-nums text-text-1" data-numeric>{fmt(d.value)}</span>
      </div>
    {/each}
  </div>
{/if}

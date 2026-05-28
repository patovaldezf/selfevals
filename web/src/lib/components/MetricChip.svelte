<script lang="ts">
  export let label: string;
  export let value: number | string | null | undefined = null;
  export let unit: string | null = null;
  export let trend: 'up' | 'down' | 'flat' | null = null;
  export let format: 'number' | 'percent' | 'delta' | 'plain' = 'number';

  function fmt(v: number | string | null | undefined): string {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'string') return v;
    if (format === 'percent') return `${(v * 100).toFixed(1)}%`;
    if (format === 'delta') {
      const sign = v > 0 ? '+' : '';
      return `${sign}${Number.isInteger(v) ? v : v.toFixed(3)}`;
    }
    return Number.isInteger(v) ? `${v}` : v.toFixed(4);
  }
</script>

<div class="flex flex-col gap-1.5 rounded-lg border border-border bg-surface px-4 py-3.5">
  <span class="text-xs uppercase tracking-wide text-text-3">{label}</span>
  <div class="flex items-baseline gap-1.5">
    <span class="text-2xl font-mono font-medium text-text-1" data-numeric>{fmt(value)}</span>
    {#if unit}
      <span class="text-xs text-text-3">{unit}</span>
    {/if}
    {#if trend === 'up'}
      <span class="text-success text-xs ml-auto" aria-label="up">▲</span>
    {:else if trend === 'down'}
      <span class="text-danger text-xs ml-auto" aria-label="down">▼</span>
    {/if}
  </div>
</div>

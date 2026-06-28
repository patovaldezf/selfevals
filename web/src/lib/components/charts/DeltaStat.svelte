<script lang="ts">
  /**
   * A headline number with its change — the unit of every dashboard stat. The
   * value reads large and tabular; the delta carries an arrow (lucide) coloured
   * by whether the change is an improvement for *this* metric (accuracy up =
   * good, cost up = bad), via the shared delta language. `goodWhen` says which
   * direction is better.
   */
  import { TrendingUp, TrendingDown, Minus } from 'lucide-svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import {
    deltaDirection,
    deltaLevel,
    levelColor,
    type ThresholdDirection
  } from '$lib/viz/thresholds';
  import { formatValue, formatDelta, type ValueFormat } from './format';

  export let label: string;
  export let value: number | string | null = null;
  export let delta: number | null = null;
  export let goodWhen: ThresholdDirection = 'higher';
  export let format: ValueFormat = 'count';
  /** Size of the headline number. */
  export let size: 'md' | 'lg' = 'lg';

  $: dir = deltaDirection(delta);
  $: level = deltaLevel(delta, goodWhen);
  $: deltaColor = level === 'neutral' ? 'var(--color-text-3)' : levelColor(level);
  $: arrow = dir === 'up' ? TrendingUp : dir === 'down' ? TrendingDown : Minus;

  function head(v: number | string | null): string {
    if (v === null) return '—';
    if (typeof v === 'string') return v;
    return formatValue(v, format);
  }
</script>

<div class="flex flex-col gap-1">
  <span class="text-xs uppercase tracking-wide text-text-3">{label}</span>
  <div class="flex items-baseline gap-2">
    <span
      class="font-mono font-medium tabular-nums text-text-1"
      style="font-size: {size === 'lg' ? 'var(--text-xl)' : 'var(--text-lg)'};"
      data-numeric
    >
      {head(value)}
    </span>
    {#if delta !== null && Number.isFinite(delta)}
      <span
        class="inline-flex items-center gap-0.5 text-xs font-medium"
        style="color: {deltaColor};"
      >
        <Icon icon={arrow} size={13} />
        <span class="tabular-nums" data-numeric>{formatDelta(delta, format)}</span>
      </span>
    {/if}
  </div>
</div>

<script lang="ts">
  /**
   * Progress ring for a single 0..1 rate (pass-rate, accuracy) read at a glance.
   * The arc paints itself by threshold verdict against `threshold.target`, and
   * the big tabular number sits in the centre. The arc sweeps in on mount via a
   * stroke-dashoffset animation. Also exported as Donut for callers that prefer
   * that name.
   */
  import { onMount } from 'svelte';
  import { thresholdLevel, levelColor, type ThresholdDirection } from '$lib/viz/thresholds';

  /** Value in 0..1. */
  export let value: number | null = null;
  export let size = 96;
  export let strokeWidth = 8;
  export let threshold: { target: number; direction?: ThresholdDirection } | null = null;
  /** Override label under the number; defaults to none. */
  export let label: string | null = null;

  $: pct = value === null || !Number.isFinite(value) ? 0 : Math.min(1, Math.max(0, value));
  $: level = threshold ? thresholdLevel(value, threshold) : 'neutral';
  $: arcColor = level === 'neutral' ? 'var(--color-brand)' : levelColor(level);

  $: r = (size - strokeWidth) / 2;
  $: circumference = 2 * Math.PI * r;
  $: center = size / 2;

  let mounted = false;
  onMount(() => {
    mounted = true;
  });
</script>

<div
  class="relative inline-flex items-center justify-center"
  style="width: {size}px; height: {size}px;"
>
  <svg width={size} height={size} viewBox="0 0 {size} {size}" class="-rotate-90" aria-hidden="true">
    <circle
      cx={center}
      cy={center}
      {r}
      fill="none"
      stroke="var(--color-surface-2)"
      stroke-width={strokeWidth}
    />
    <circle
      cx={center}
      cy={center}
      {r}
      fill="none"
      stroke={arcColor}
      stroke-width={strokeWidth}
      stroke-linecap="round"
      stroke-dasharray={circumference}
      stroke-dashoffset={mounted ? circumference * (1 - pct) : circumference}
      style="transition: stroke-dashoffset 0.55s var(--ease-out);"
      class="ring-arc"
    />
  </svg>
  <div class="absolute inset-0 flex flex-col items-center justify-center">
    <span
      class="font-mono font-medium tabular-nums text-text-1"
      style="font-size: {size > 80 ? 'var(--text-md)' : 'var(--text-sm)'};"
      data-numeric
    >
      {value === null || !Number.isFinite(value) ? '—' : `${(pct * 100).toFixed(0)}%`}
    </span>
    {#if label}
      <span class="text-[10px] text-text-3">{label}</span>
    {/if}
  </div>
</div>

<style>
  @media (prefers-reduced-motion: reduce) {
    .ring-arc {
      transition: none !important;
    }
  }
</style>

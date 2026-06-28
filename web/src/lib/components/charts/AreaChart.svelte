<script lang="ts">
  /**
   * Filled-area variant for single-series trends (cost, tokens, latency over
   * time) where the magnitude under the curve reads better than a bare line.
   * Shares the math style of LineChart but keeps it single-series for a clean
   * gradient fill. The fill is a low-opacity tint of the stroke (or the
   * threshold colour when a target is given).
   */
  import { onMount } from 'svelte';
  import { thresholdLevel, levelColor, type ThresholdDirection } from '$lib/viz/thresholds';
  import { formatValue, svgId, type ValueFormat } from './format';
  import type { Point } from './types';

  export let points: Point[] = [];
  export let height = 160;
  export let format: ValueFormat = 'count';
  export let stroke = 'var(--color-brand)';
  export let threshold: { target: number; direction?: ThresholdDirection } | null = null;
  export let showAxis = true;

  const PAD_L = 40;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 18;

  let width = 600;
  let hoverIdx: number | null = null;
  const gid = svgId('ac-grad');

  $: clean = points.filter((p) => Number.isFinite(p.y));
  $: latest = clean[clean.length - 1] ?? null;
  $: level = threshold && latest ? thresholdLevel(latest.y, threshold) : 'neutral';
  $: lineColor = threshold && level !== 'neutral' ? levelColor(level) : stroke;

  $: domain = (() => {
    const ys = clean.map((p) => p.y);
    if (threshold) ys.push(threshold.target);
    let lo = ys.length ? Math.min(...ys) : 0;
    let hi = ys.length ? Math.max(...ys) : 1;
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.1;
    return { lo: lo - pad, hi: hi + pad };
  })();

  $: innerW = Math.max(1, width - PAD_L - PAD_R);
  $: innerH = Math.max(1, height - PAD_T - PAD_B);

  function sx(i: number): number {
    if (clean.length <= 1) return PAD_L + innerW / 2;
    return PAD_L + (i / (clean.length - 1)) * innerW;
  }
  function sy(v: number): number {
    const t = (v - domain.lo) / (domain.hi - domain.lo || 1);
    return PAD_T + innerH - t * innerH;
  }

  $: line = clean
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(p.y).toFixed(1)}`)
    .join(' ');
  $: area =
    clean.length >= 2
      ? `${line} L${sx(clean.length - 1).toFixed(1)},${(PAD_T + innerH).toFixed(1)} L${sx(0).toFixed(1)},${(PAD_T + innerH).toFixed(1)} Z`
      : '';

  $: yTicks = showAxis ? [domain.hi, domain.lo] : [];

  function onMove(e: MouseEvent) {
    if (clean.length < 1) return;
    const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const rel = (px - PAD_L) / innerW;
    hoverIdx = Math.min(clean.length - 1, Math.max(0, Math.round(rel * (clean.length - 1))));
  }

  let mounted = false;
  onMount(() => {
    mounted = true;
  });
</script>

{#if clean.length < 2}
  <p class="py-8 text-center text-sm text-text-3">Not enough data to plot.</p>
{:else}
  <div bind:clientWidth={width} class="relative w-full">
    <svg
      {width}
      {height}
      viewBox="0 0 {width} {height}"
      class="block w-full"
      role="img"
      aria-label="Area trend"
      on:mousemove={onMove}
      on:mouseleave={() => (hoverIdx = null)}
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color={lineColor} stop-opacity="0.18" />
          <stop offset="100%" stop-color={lineColor} stop-opacity="0" />
        </linearGradient>
      </defs>

      {#each yTicks as ty}
        <line
          x1={PAD_L}
          x2={width - PAD_R}
          y1={sy(ty)}
          y2={sy(ty)}
          stroke="var(--color-chart-grid)"
          stroke-width="1"
        />
        <text
          x={PAD_L - 6}
          y={sy(ty) + 3}
          text-anchor="end"
          class="fill-[var(--color-text-3)]"
          style="font-size: 10px; font-variant-numeric: tabular-nums;"
          >{formatValue(ty, format)}</text
        >
      {/each}

      {#if threshold}
        <line
          x1={PAD_L}
          x2={width - PAD_R}
          y1={sy(threshold.target)}
          y2={sy(threshold.target)}
          stroke={levelColor(level === 'neutral' ? 'ok' : level)}
          stroke-width="1.25"
          stroke-dasharray="4 3"
          opacity="0.7"
        />
      {/if}

      <path d={area} fill="url(#{gid})" class="ac-area" class:in={mounted} />
      <path
        d={line}
        fill="none"
        stroke={lineColor}
        stroke-width="1.75"
        stroke-linejoin="round"
        stroke-linecap="round"
      />

      {#if hoverIdx !== null && clean[hoverIdx]}
        <line
          x1={sx(hoverIdx)}
          x2={sx(hoverIdx)}
          y1={PAD_T}
          y2={PAD_T + innerH}
          stroke="var(--color-border-strong)"
          stroke-width="1"
        />
        <circle
          cx={sx(hoverIdx)}
          cy={sy(clean[hoverIdx].y)}
          r="3"
          fill={lineColor}
          stroke="var(--color-surface)"
          stroke-width="1.5"
        />
      {/if}
    </svg>

    {#if hoverIdx !== null && clean[hoverIdx]}
      <div
        class="pointer-events-none absolute top-1 rounded-md border border-border bg-surface px-2 py-1 shadow-[var(--shadow-2)]"
        style="left: {Math.min(width - 90, Math.max(4, sx(hoverIdx) - 45))}px;"
      >
        <div class="font-mono text-xs tabular-nums text-text-1" data-numeric>
          {formatValue(clean[hoverIdx].y, format)}
        </div>
        <div class="text-[10px] text-text-3">{clean[hoverIdx].x}</div>
      </div>
    {/if}
  </div>
{/if}

<style>
  .ac-area {
    opacity: 0;
  }
  .ac-area.in {
    animation: ac-fade 0.4s var(--ease-out) forwards;
  }
  @keyframes ac-fade {
    to {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .ac-area {
      opacity: 1;
    }
    .ac-area.in {
      animation: none;
    }
  }
</style>

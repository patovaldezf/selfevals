<script lang="ts">
  /**
   * Metric / accuracy over time — the workhorse of the "system breathing"
   * dashboards. Plots one or more series; when a `threshold` is given it draws a
   * dashed target line and tints the latest point by how it sits against that
   * target (green/amber/red via the shared threshold language). Hover snaps a
   * vertical crosshair to the nearest point and shows a readout. The line draws
   * itself once on mount (stroke-dashoffset), never on re-render, so live data
   * updates don't re-trigger the animation.
   *
   * No chart dep — SVG by hand for full control of the threshold feel and zero
   * bundle weight.
   */
  import { onMount } from 'svelte';
  import { thresholdLevel, levelColor, type ThresholdDirection } from '$lib/viz/thresholds';
  import { formatValue, svgId, type ValueFormat } from './format';
  import type { Point, Series } from './types';

  /** Either a single `points` array or `series` of them. */
  export let points: Point[] | null = null;
  export let series: Series[] | null = null;
  export let height = 200;
  export let format: ValueFormat = 'count';
  /** Optional target: draws a dashed line and colours the latest point. */
  export let threshold: { target: number; direction?: ThresholdDirection } | null = null;
  /** Pin the y-domain instead of deriving it from the data. */
  export let yMin: number | null = null;
  export let yMax: number | null = null;
  export let showAxis = true;

  // Left pad fits a 6-char y-label ("100.0%") right-aligned at PAD_L - 6 without
  // clipping the leading digit.
  const PAD_L = 48;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 20;

  let width = 600;
  let hoverIdx: number | null = null;
  const clipId = svgId('lc-clip');

  // Normalise inputs to a list of series with resolved colours.
  $: resolved = (() => {
    const list: Series[] = series ?? (points ? [{ points }] : []);
    return list
      .map((s, i) => ({
        name: s.name,
        color: s.color ?? `var(--color-chart-${(i % 3) + 1})`,
        points: s.points.filter((p) => Number.isFinite(p.y))
      }))
      .filter((s) => s.points.length > 0);
  })();

  $: longest = resolved.reduce((m, s) => Math.max(m, s.points.length), 0);

  $: domain = (() => {
    const ys = resolved.flatMap((s) => s.points.map((p) => p.y));
    if (threshold) ys.push(threshold.target);
    let lo = yMin ?? (ys.length ? Math.min(...ys) : 0);
    let hi = yMax ?? (ys.length ? Math.max(...ys) : 1);
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }
    // A touch of headroom so the line never kisses the frame.
    const pad = (hi - lo) * 0.08;
    let padLo = lo - pad;
    let padHi = hi + pad;
    // A rate metric (pass@k, accuracy) tops out at 1.0 / bottoms at 0 — never
    // let the auto-padding push the axis past those, or labels read like 105.3%.
    if (format === 'percent') {
      if (padHi > 1) padHi = 1;
      if (padLo < 0) padLo = 0;
    }
    return { lo: yMin ?? padLo, hi: yMax ?? padHi };
  })();

  $: innerW = Math.max(1, width - PAD_L - PAD_R);
  $: innerH = Math.max(1, height - PAD_T - PAD_B);

  function sx(i: number, n: number): number {
    if (n <= 1) return PAD_L + innerW / 2;
    return PAD_L + (i / (n - 1)) * innerW;
  }
  function sy(v: number): number {
    const t = (v - domain.lo) / (domain.hi - domain.lo || 1);
    return PAD_T + innerH - t * innerH;
  }

  function linePath(pts: Point[]): string {
    return pts
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(i, pts.length).toFixed(1)},${sy(p.y).toFixed(1)}`)
      .join(' ');
  }

  // Y ticks: lo, mid, hi — enough to read the scale without clutter.
  $: yTicks = showAxis ? [domain.hi, (domain.hi + domain.lo) / 2, domain.lo] : [];

  $: thresholdY = threshold ? sy(threshold.target) : null;

  // Latest point of the primary series, coloured by threshold.
  $: primary = resolved[0];
  $: latest = primary?.points[primary.points.length - 1] ?? null;
  $: latestLevel = threshold && latest ? thresholdLevel(latest.y, threshold) : 'neutral';

  function onMove(e: MouseEvent) {
    if (!primary || longest < 1) return;
    const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    const rel = (px - PAD_L) / innerW;
    const idx = Math.round(rel * (longest - 1));
    hoverIdx = Math.min(longest - 1, Math.max(0, idx));
  }
  function onLeave() {
    hoverIdx = null;
  }

  function tipX(): number {
    if (hoverIdx === null) return 0;
    return Math.min(width - 90, Math.max(4, sx(hoverIdx, longest) - 45));
  }

  // Draw-on-mount: only enable the dash animation after first paint.
  let mounted = false;
  let reduceMotion = false;
  onMount(() => {
    reduceMotion =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    mounted = true;
  });
</script>

{#if resolved.length === 0}
  <p class="py-8 text-center text-sm text-text-3">Not enough data to plot.</p>
{:else}
  <div bind:clientWidth={width} class="relative w-full">
    <svg
      {width}
      {height}
      viewBox="0 0 {width} {height}"
      class="block w-full"
      role="img"
      aria-label={threshold ? 'Metric over time with target' : 'Metric over time'}
      on:mousemove={onMove}
      on:mouseleave={onLeave}
    >
      <defs>
        <clipPath id={clipId}>
          <rect x={PAD_L} y={PAD_T} width={innerW} height={innerH} />
        </clipPath>
      </defs>

      {#if showAxis}
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
          >
            {formatValue(ty, format)}
          </text>
        {/each}
      {/if}

      {#if thresholdY !== null}
        <line
          x1={PAD_L}
          x2={width - PAD_R}
          y1={thresholdY}
          y2={thresholdY}
          stroke={levelColor(latestLevel === 'neutral' ? 'ok' : latestLevel)}
          stroke-width="1.25"
          stroke-dasharray="4 3"
          opacity="0.7"
        />
      {/if}

      {#each resolved as s (s.name ?? s.color)}
        <path
          d={linePath(s.points)}
          fill="none"
          stroke={s.color}
          stroke-width="1.75"
          stroke-linejoin="round"
          stroke-linecap="round"
          clip-path="url(#{clipId})"
          class="lc-line"
          class:animate={mounted && !reduceMotion}
        />
      {/each}

      <!-- Latest point dot, coloured by threshold verdict. -->
      {#if latest && primary}
        <circle
          cx={sx(primary.points.length - 1, primary.points.length)}
          cy={sy(latest.y)}
          r="3"
          fill={threshold ? levelColor(latestLevel) : primary.color}
          stroke="var(--color-surface)"
          stroke-width="1.5"
        />
      {/if}

      <!-- Hover crosshair + markers. -->
      {#if hoverIdx !== null && primary}
        <line
          x1={sx(hoverIdx, longest)}
          x2={sx(hoverIdx, longest)}
          y1={PAD_T}
          y2={PAD_T + innerH}
          stroke="var(--color-border-strong)"
          stroke-width="1"
        />
        {#each resolved as s}
          {#if s.points[hoverIdx]}
            <circle
              cx={sx(hoverIdx, longest)}
              cy={sy(s.points[hoverIdx].y)}
              r="3"
              fill={s.color}
              stroke="var(--color-surface)"
              stroke-width="1.5"
            />
          {/if}
        {/each}
      {/if}
    </svg>

    {#if hoverIdx !== null && primary?.points[hoverIdx]}
      <div
        class="pointer-events-none absolute top-1 rounded-md border border-border bg-surface px-2 py-1 shadow-[var(--shadow-2)]"
        style="left: {tipX()}px;"
      >
        <div class="font-mono text-xs tabular-nums text-text-1" data-numeric>
          {formatValue(primary.points[hoverIdx].y, format)}
        </div>
        <div class="text-[10px] text-text-3">
          {primary.points[hoverIdx].x}
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  /* Draw-in: stroke reveal on mount. The dash length is generous so any line
     fits; we only flip the offset once `animate` is set after first paint. */
  .lc-line {
    stroke-dasharray: 2000;
    stroke-dashoffset: 0;
  }
  .lc-line.animate {
    stroke-dashoffset: 2000;
    animation: lc-draw 0.45s var(--ease-out) forwards;
  }
  @keyframes lc-draw {
    to {
      stroke-dashoffset: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .lc-line.animate {
      animation: none;
      stroke-dashoffset: 0;
    }
  }
</style>

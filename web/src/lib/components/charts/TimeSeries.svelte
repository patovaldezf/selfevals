<script lang="ts">
  /**
   * A filled area + line time series. Larger sibling of Sparkline for the
   * metrics dashboard. No chart dep. Points are `{t, value}`; we draw a
   * normalized path with a soft gradient fill and an optional hover readout.
   * Stays grayscale; the area fill is a low-opacity tint of the line color.
   */
  export let points: { t: string; value: number }[] = [];
  export let height = 120;
  export let stroke = 'var(--color-chart-1)';
  export let format: 'count' | 'percent' | 'usd' = 'count';

  let width = 600;
  const PAD = 4;

  $: clean = points.filter((p) => Number.isFinite(p.value));
  $: geom = (() => {
    if (clean.length < 2) return null;
    const vals = clean.map((p) => p.value);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = max - min || 1;
    const innerH = height - PAD * 2;
    const stepX = width / (clean.length - 1);
    const coords = clean.map((p, i) => ({
      x: i * stepX,
      y: PAD + innerH - ((p.value - min) / span) * innerH
    }));
    const line = coords
      .map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
      .join(' ');
    const area = `${line} L${width},${height} L0,${height} Z`;
    return { line, area, min, max };
  })();

  function fmt(v: number): string {
    if (format === 'percent') return `${(v * 100).toFixed(1)}%`;
    if (format === 'usd') return `$${v.toFixed(2)}`;
    return Number.isInteger(v) ? `${v}` : v.toFixed(2);
  }

  const gid = `ts-grad-${Math.random().toString(36).slice(2, 9)}`;
</script>

{#if !geom}
  <p class="py-8 text-center text-sm text-text-3">Not enough data to plot.</p>
{:else}
  <div bind:clientWidth={width} class="w-full">
    <div class="mb-1 flex justify-between text-xs text-text-3">
      <span>{fmt(geom.min)}</span>
      <span>{fmt(geom.max)}</span>
    </div>
    <svg
      {width}
      {height}
      viewBox="0 0 {width} {height}"
      preserveAspectRatio="none"
      class="block w-full"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color={stroke} stop-opacity="0.14" />
          <stop offset="100%" stop-color={stroke} stop-opacity="0" />
        </linearGradient>
      </defs>
      <path d={geom.area} fill="url(#{gid})" />
      <path
        d={geom.line}
        fill="none"
        {stroke}
        stroke-width="1.5"
        stroke-linejoin="round"
        stroke-linecap="round"
      />
    </svg>
  </div>
{/if}

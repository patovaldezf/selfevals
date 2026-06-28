<script lang="ts">
  /**
   * Minimal sparkline — N values normalized to a tiny SVG path. Lives in the
   * chart kit so tables and stat cards share one implementation. Stays neutral
   * by default (accent/threshold colour is the caller's choice). With `endDot`
   * it marks the latest value; with `threshold` that dot is coloured by verdict.
   * Hover is intentionally omitted at this size — sparklines pair with a visible
   * number, so the line only conveys shape.
   */
  import { thresholdLevel, levelColor, type ThresholdDirection } from '$lib/viz/thresholds';

  export let values: number[] = [];
  export let width = 120;
  export let height = 28;
  export let stroke = 'currentColor';
  export let endDot = false;
  export let threshold: { target: number; direction?: ThresholdDirection } | null = null;

  const PAD = 2;

  $: clean = values.filter((v) => Number.isFinite(v));
  $: geom = (() => {
    if (clean.length < 2) return null;
    const min = Math.min(...clean);
    const max = Math.max(...clean);
    const span = max - min || 1;
    const stepX = (width - PAD * 2) / (clean.length - 1);
    const coords = clean.map((v, i) => ({
      x: PAD + i * stepX,
      y: PAD + (height - PAD * 2) - ((v - min) / span) * (height - PAD * 2)
    }));
    const path = coords
      .map((c, i) => `${i === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`)
      .join(' ');
    return { path, last: coords[coords.length - 1] };
  })();

  $: dotColor =
    threshold && clean.length
      ? levelColor(thresholdLevel(clean[clean.length - 1], threshold))
      : stroke;
</script>

<svg {width} {height} viewBox="0 0 {width} {height}" class="text-text-2" aria-hidden="true">
  {#if geom}
    <path
      d={geom.path}
      fill="none"
      {stroke}
      stroke-width="1.25"
      stroke-linejoin="round"
      stroke-linecap="round"
    />
    {#if endDot}
      <circle cx={geom.last.x} cy={geom.last.y} r="2" fill={dotColor} />
    {/if}
  {/if}
</svg>

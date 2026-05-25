<script lang="ts">
  /**
   * Minimal sparkline with no chart dep. SVG path of N values, normalized.
   * Stay grayscale — accent color is reserved for primary actions.
   */
  export let values: number[] = [];
  export let width = 120;
  export let height = 28;
  export let stroke = 'currentColor';

  $: path = (() => {
    if (values.length < 2) return '';
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = width / (values.length - 1);
    return values
      .map((v, i) => {
        const x = i * stepX;
        const y = height - ((v - min) / span) * height;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  })();
</script>

<svg
  {width}
  {height}
  viewBox="0 0 {width} {height}"
  class="text-text-2"
  aria-hidden="true"
>
  {#if path}
    <path
      d={path}
      fill="none"
      stroke={stroke}
      stroke-width="1.25"
      stroke-linejoin="round"
      stroke-linecap="round"
    />
  {/if}
</svg>

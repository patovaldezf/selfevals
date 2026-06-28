<script lang="ts">
  /** Alias of StatRing for callers who think in "donut" terms. Same API. */
  import StatRing from './StatRing.svelte';
  import type { ThresholdDirection } from '$lib/viz/thresholds';

  export let value: number | null = null;
  export let size = 96;
  export let strokeWidth = 8;
  export let threshold: { target: number; direction?: ThresholdDirection } | null = null;
  export let label: string | null = null;
</script>

<StatRing {value} {size} {strokeWidth} {threshold} {label} />

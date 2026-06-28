<script lang="ts">
  /** A number that animates to its target when it changes — the small touch that
   *  makes a live metric feel alive as iterations land. Uses a tweened store
   *  (ease-out, ~400ms) so a jump from 0.33 → 0.67 rolls rather than snaps.
   *  Respects prefers-reduced-motion by snapping. Format matches the chart
   *  language (percent/count/usd). Renders tabular so digits don't jitter. */
  import { tweened } from 'svelte/motion';
  import { cubicOut } from 'svelte/easing';
  import { formatValue, type ValueFormat } from './format';

  export let value: number | null = null;
  export let format: ValueFormat = 'percent';
  export let duration = 420;

  const reduce =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

  const n = tweened(value ?? 0, { duration: reduce ? 0 : duration, easing: cubicOut });

  // Re-target whenever the input changes; tweened interrupts cleanly toward the
  // newest value, so rapid updates during a live run never look "broken".
  $: if (value !== null) n.set(value);
</script>

{#if value === null}
  <span class="count" data-numeric>—</span>
{:else}
  <span class="count" data-numeric>{formatValue($n, format)}</span>
{/if}

<style>
  .count {
    font-variant-numeric: tabular-nums;
    font-family: var(--font-mono);
  }
</style>

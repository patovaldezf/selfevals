<script lang="ts">
  /** Loading placeholder that mirrors the shape it replaces — a line of text, a
   *  card, a table row, or a chart block. Render `count` of them to fill a list.
   *  Variants set sensible default dimensions; `width`/`height` override. */
  export let variant: 'line' | 'card' | 'table-row' | 'chart' = 'line';
  export let width: string | null = null;
  export let height: string | null = null;
  export let count = 1;

  const DEFAULTS: Record<typeof variant, { w: string; h: string; r: string }> = {
    line: { w: '100%', h: '0.85rem', r: 'var(--radius-sm)' },
    card: { w: '100%', h: '5.5rem', r: 'var(--radius-lg)' },
    'table-row': { w: '100%', h: '2.5rem', r: 'var(--radius-sm)' },
    chart: { w: '100%', h: '12rem', r: 'var(--radius-lg)' }
  };

  $: d = DEFAULTS[variant];
  $: items = Array.from({ length: Math.max(1, count) });
</script>

<span class="stack" aria-hidden="true">
  {#each items as _, i (i)}
    <span
      class="skeleton block"
      style="width: {width ?? d.w}; height: {height ?? d.h}; border-radius: {d.r};"
    ></span>
  {/each}
</span>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    width: 100%;
  }
  .skeleton {
    background: linear-gradient(
      90deg,
      var(--color-surface-2) 25%,
      var(--color-border) 37%,
      var(--color-surface-2) 63%
    );
    background-size: 400% 100%;
    animation: shimmer 1.4s ease-in-out infinite;
  }
  @keyframes shimmer {
    0% {
      background-position: 100% 50%;
    }
    100% {
      background-position: 0 50%;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .skeleton {
      animation: none;
    }
  }
</style>

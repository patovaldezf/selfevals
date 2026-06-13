<script lang="ts">
  /** A shimmer placeholder. Compose several to mirror the shape of the
   *  content that's loading — a row of bars for a table, a block for a card. */
  export let width: string = '100%';
  export let height: string = '1rem';
  export let radius: string = 'var(--radius-sm)';
</script>

<span
  class="skeleton block"
  style="width: {width}; height: {height}; border-radius: {radius};"
  aria-hidden="true"
></span>

<style>
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

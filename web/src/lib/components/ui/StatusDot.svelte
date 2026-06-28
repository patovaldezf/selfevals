<script lang="ts">
  /** Experiment / iteration state as a coloured dot. The state machine maps to
   *  the threshold language: running pulses brand (work in flight), completed is
   *  ok, failed/aborted is bad, paused warns, queued/draft sit neutral. Pair it
   *  with a text label — colour reinforces, it doesn't carry meaning alone. */
  export let state: string = 'draft';
  export let size: number = 8;

  type Tone = 'brand' | 'ok' | 'bad' | 'warn' | 'neutral';
  const TONES: Record<string, Tone> = {
    running: 'brand',
    queued: 'neutral',
    draft: 'neutral',
    completed: 'ok',
    done: 'ok',
    succeeded: 'ok',
    failed: 'bad',
    aborted: 'bad',
    error: 'bad',
    cancelled: 'bad',
    paused: 'warn'
  };

  $: tone = TONES[state?.toLowerCase()] ?? 'neutral';
  $: pulse = tone === 'brand';
</script>

<span class="dot dot-{tone}" class:pulse style="--dot-size: {size}px;" role="img" aria-label={state}
></span>

<style>
  .dot {
    display: inline-block;
    width: var(--dot-size);
    height: var(--dot-size);
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot-brand {
    background: var(--color-brand);
  }
  .dot-ok {
    background: var(--color-ok);
  }
  .dot-bad {
    background: var(--color-bad);
  }
  .dot-warn {
    background: var(--color-warn);
  }
  .dot-neutral {
    background: var(--color-text-3);
  }
  /* A soft expanding halo signals "live" without the jitter of a blink. */
  .pulse {
    position: relative;
  }
  .pulse::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: inherit;
    animation: pulse-ring 1.8s ease-out infinite;
  }
  @keyframes pulse-ring {
    0% {
      transform: scale(1);
      opacity: 0.5;
    }
    70%,
    100% {
      transform: scale(2.6);
      opacity: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .pulse::after {
      animation: none;
    }
  }
</style>

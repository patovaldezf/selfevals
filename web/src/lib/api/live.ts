/**
 * Live polling helper — the app's one place for "call this every N ms while the
 * tab is visible". Replaces scattered setInterval blocks that kept hitting the
 * API from a backgrounded tab (wasted requests, stale bursts on refocus).
 *
 * `createPoller(fn, intervalMs)` runs `fn` immediately, then on an interval. It
 * pauses when the tab is hidden (visibilitychange) and fires once on the way
 * back to catch up, so a run that finished while you were away shows as done the
 * moment you return. `stop()` tears everything down; safe to call twice. SSR-safe
 * (no-ops without `window`).
 *
 * This is the data-layer helper the plan called for instead of adopting
 * svelte-query — small, explicit, no cache to reason about.
 */

export type Poller = { stop: () => void };

export function createPoller(
  fn: () => void | Promise<void>,
  intervalMs: number
): Poller {
  if (typeof window === 'undefined') {
    return { stop: () => {} };
  }

  let timer: ReturnType<typeof setInterval> | null = null;
  let stopped = false;

  const tick = () => {
    void fn();
  };

  const start = () => {
    if (timer !== null || stopped) return;
    tick();
    timer = setInterval(tick, intervalMs);
  };

  const pause = () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };

  const onVisibility = () => {
    if (document.hidden) pause();
    else start();
  };

  document.addEventListener('visibilitychange', onVisibility);
  // Kick off unless we loaded into a hidden tab.
  if (!document.hidden) start();

  return {
    stop() {
      stopped = true;
      pause();
      document.removeEventListener('visibilitychange', onVisibility);
    }
  };
}

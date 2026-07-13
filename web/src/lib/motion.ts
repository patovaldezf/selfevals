/**
 * Motion presets — one place for the app's spatial transitions so overlays
 * share a single feel instead of six magic numbers scattered across components.
 *
 * The rule (from app.css): short + ease-out for repeated/UI motion; a gentle
 * spring for milestones and spatial entrances (a drawer arriving, a modal
 * landing). These presets encode that: `overlayScrim` is the calm fade,
 * `drawerPanel`/`modalPanel` carry the spring so panels feel like they have
 * weight and settle rather than snap.
 *
 * Reduced-motion: when the user asks for less, we collapse every preset to a
 * near-instant fade with no travel — same code path, no motion. Components pass
 * `reduced()` results straight into `transition:fly`/`transition:scale`.
 */

/** JS easing mirroring app.css `--ease-spring: cubic-bezier(0.34,1.56,0.64,1)` —
 *  a soft overshoot that settles. Used so Svelte's fly/scale match the CSS feel. */
export function spring(t: number): number {
  const c1 = 1.70158;
  const c3 = c1 + 1;
  return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}

/** JS easing mirroring `--ease-out: cubic-bezier(0.16,1,0.3,1)` — fast out,
 *  long gentle tail. The default for scrims and quick UI motion. */
export function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 4);
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  );
}

/** Collapse a preset to an instant, travel-free fade when reduced-motion is on. */
function reduced<T extends Record<string, unknown>>(full: T): T {
  if (!prefersReducedMotion()) return full;
  return { ...full, duration: 0, x: 0, y: 0, start: 1 } as T;
}

/** Scrim behind an overlay — a calm fade, never springs. */
export function overlayScrim() {
  return reduced({ duration: 160, easing: easeOut });
}

/** Right-hand drawer panel — slides in from the edge on a spring. Pass to
 *  `transition:fly`. */
export function drawerPanel() {
  return reduced({ x: 28, duration: 340, easing: spring, opacity: 0 });
}

/** Centered modal panel — lands with a spring scale. Pass to `transition:scale`. */
export function modalPanel() {
  return reduced({ start: 0.96, opacity: 0, duration: 260, easing: spring });
}

/** Command palette / popover — drops in from slightly above on ease-out (fast,
 *  repeated action, so no spring). Pass to `transition:fly`. */
export function popoverPanel() {
  return reduced({ y: -8, duration: 180, easing: easeOut, opacity: 0 });
}

/** Per-item stagger delay for a list reveal, capped so long lists don't crawl. */
export function staggerDelay(index: number, step = 24, max = 200): number {
  if (prefersReducedMotion()) return 0;
  return Math.min(index * step, max);
}

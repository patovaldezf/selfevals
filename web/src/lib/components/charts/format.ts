/**
 * Shared value formatting for the data-viz kit, so every chart speaks the same
 * number language. `count` keeps integers tight and trims float noise; `percent`
 * assumes a 0..1 fraction; `usd` and `ms` cover cost/latency axes.
 */
export type ValueFormat = 'count' | 'percent' | 'usd' | 'ms' | 'plain';

export function formatValue(v: number, format: ValueFormat = 'count'): string {
  if (!Number.isFinite(v)) return '—';
  switch (format) {
    case 'percent':
      return `${(v * 100).toFixed(1)}%`;
    case 'usd':
      return `$${v < 1 ? v.toFixed(4) : v.toFixed(2)}`;
    case 'ms':
      return v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`;
    case 'plain':
      return Number.isInteger(v) ? `${v}` : v.toFixed(2);
    default:
      return Number.isInteger(v) ? `${v}` : v.toFixed(2);
  }
}

/** Signed variant for deltas (+0.12, -3). */
export function formatDelta(v: number, format: ValueFormat = 'count'): string {
  if (!Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${formatValue(v, format)}`;
}

/** Stable-ish id for SVG defs (gradients/clips). Mirrors TimeSeries' approach;
 *  Math.random is fine here — these ids never need to survive a reload. */
export function svgId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
}

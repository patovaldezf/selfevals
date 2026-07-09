/**
 * Number/delta formatting shared by the experiment page's iterations table,
 * compare tab, and results tab — extracted so each doesn't duplicate the
 * same four helpers. `deltaColor` reuses the threshold language from
 * `./thresholds` so a positive/negative change reads the same green/amber/red
 * as charts and stat cards elsewhere.
 */
import type { CompareResponse } from '$lib/api/client';
import { levelColor, deltaLevel, type ThresholdDirection } from './thresholds';

export function fmtNumber(value: number | null, digits = 4): string {
  if (value === null) return '—';
  if (Number.isInteger(value)) return `${value}`;
  return value.toFixed(digits);
}

export function fmtDelta(value: number | null): string {
  if (value === null) return '—';
  if (Math.abs(value) < 1e-9) return '0';
  const sign = value > 0 ? '+' : '';
  return `${sign}${fmtNumber(value, 3)}`;
}

/** Δ colour via the shared threshold language: improvement (in the metric's
 *  good direction) is green, regression red, neutral grey. */
export function deltaColor(value: number | null, targetDirection: ThresholdDirection): string {
  return levelColor(deltaLevel(value, targetDirection));
}

/** Recommendation banner copy, derived from the server's verdict. */
export function recommendationText(r: CompareResponse['recommendation']): string {
  switch (r.kind) {
    case 'winner':
      return `${r.winner} is better: ${r.metric_name} ${fmtDelta(r.delta)} (${fmtNumber(
        r.a_value
      )} → ${fmtNumber(r.b_value)})`;
    case 'tie':
      return `A and B tie on ${r.metric_name} (${fmtNumber(r.a_value)}) — compare guardrails or failure modes to decide.`;
    case 'different_metric':
      return `Different primary metrics (A=${r.a_metric_name} vs B=${r.b_metric_name}); no recommendation.`;
    default:
      return 'No primary metric to compare.';
  }
}

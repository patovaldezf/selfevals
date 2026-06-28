/**
 * Threshold language — the single source of truth for "is this number good?".
 *
 * Every metric with a target paints ok / warn / bad. The rule is centralised
 * here so a chart, a stat card, and a badge all agree on the colour, and so we
 * never hardcode a green hex in a component. Components read the returned
 * `level` and map it to the CSS vars `--color-ok|warn|bad` (and their `-subtle`
 * / `-fg` companions) defined in app.css.
 *
 * A metric is described by a target value and a direction:
 *   - 'higher' → pass-rate, accuracy, F1   (≥ target is good)
 *   - 'lower'  → cost, latency, error-rate (≤ target is good)
 * The `warn` band is the near-miss zone just short of the target.
 */

export type ThresholdLevel = 'ok' | 'warn' | 'bad' | 'neutral';
export type ThresholdDirection = 'higher' | 'lower';

/** Map a target's comparison operator (as the API reports it on
 *  `primary_target.operator`) to a threshold direction. `>=`/`>` mean bigger is
 *  better; `<=`/`<` mean smaller is better. Anything else (==, unknown) has no
 *  good side, so we default to 'higher' and let the caller decide. */
export function directionFromOperator(operator: string | null | undefined): ThresholdDirection {
  if (operator === '<' || operator === '<=') return 'lower';
  return 'higher';
}

export type ThresholdSpec = {
  target: number;
  direction?: ThresholdDirection;
  /** Half-width of the warn band as a fraction of the target. Default 0.1
   *  (i.e. within 10% of target on the wrong side = warn, beyond = bad). */
  warnBand?: number;
};

/** Classify a value against a target. Returns 'neutral' when there is no
 *  meaningful target (null/undefined/NaN) so callers fall back to chart-neutral
 *  colours rather than inventing a verdict. */
export function thresholdLevel(
  value: number | null | undefined,
  spec: ThresholdSpec | null | undefined
): ThresholdLevel {
  if (value === null || value === undefined || Number.isNaN(value)) return 'neutral';
  if (!spec || spec.target === null || spec.target === undefined || Number.isNaN(spec.target))
    return 'neutral';

  const direction = spec.direction ?? 'higher';
  const band = Math.abs((spec.warnBand ?? 0.1) * spec.target);

  if (direction === 'higher') {
    if (value >= spec.target) return 'ok';
    if (value >= spec.target - band) return 'warn';
    return 'bad';
  }
  // 'lower' is better
  if (value <= spec.target) return 'ok';
  if (value <= spec.target + band) return 'warn';
  return 'bad';
}

/** CSS var for the solid tone of a level (e.g. a bar fill, a ring stroke). */
export function levelColor(level: ThresholdLevel): string {
  switch (level) {
    case 'ok':
      return 'var(--color-ok)';
    case 'warn':
      return 'var(--color-warn)';
    case 'bad':
      return 'var(--color-bad)';
    default:
      return 'var(--color-chart-2)';
  }
}

/** Low-alpha companion fill (badge background, ring track, area gradient). */
export function levelSubtle(level: ThresholdLevel): string {
  switch (level) {
    case 'ok':
      return 'var(--color-ok-subtle)';
    case 'warn':
      return 'var(--color-warn-subtle)';
    case 'bad':
      return 'var(--color-bad-subtle)';
    default:
      return 'var(--color-surface-2)';
  }
}

/** Readable foreground when text sits on the solid tone. */
export function levelFg(level: ThresholdLevel): string {
  switch (level) {
    case 'ok':
      return 'var(--color-ok-fg)';
    case 'warn':
      return 'var(--color-warn-fg)';
    case 'bad':
      return 'var(--color-bad-fg)';
    default:
      return 'var(--color-text-1)';
  }
}

export type DeltaDirection = 'up' | 'down' | 'flat';

/** Direction of a change, with a dead-zone so tiny noise reads as flat. */
export function deltaDirection(delta: number | null | undefined, epsilon = 1e-9): DeltaDirection {
  if (delta === null || delta === undefined || Number.isNaN(delta)) return 'flat';
  if (delta > epsilon) return 'up';
  if (delta < -epsilon) return 'down';
  return 'flat';
}

/**
 * Colour of a delta. Whether "up" is good depends on the metric: accuracy up is
 * good, cost up is bad. `goodWhen` says which direction is an improvement.
 * Returns a threshold level so the same green/amber/red language applies.
 */
export function deltaLevel(
  delta: number | null | undefined,
  goodWhen: ThresholdDirection = 'higher',
  epsilon = 1e-9
): ThresholdLevel {
  const dir = deltaDirection(delta, epsilon);
  if (dir === 'flat') return 'neutral';
  const improved = goodWhen === 'higher' ? dir === 'up' : dir === 'down';
  return improved ? 'ok' : 'bad';
}

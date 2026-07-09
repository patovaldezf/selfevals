/**
 * Semantic tone tokens — the shared palette for domain badges/chips whose
 * meaning is categorical, not a threshold verdict.
 *
 * `thresholds.ts` answers "is this number good?" (ok/warn/bad against a target).
 * This answers "what kind of thing is this?" for labels like a grader grade
 * (pass/fail/partial) or a decision outcome (keep/reject/investigate). Both draw
 * from the SAME app.css vars (`--color-ok|warn|bad|brand` + `-subtle`/`-fg`
 * companions, `--color-surface-*`, `--color-text-*`) so every badge across the
 * app agrees on colour AND honours dark mode automatically — no component ever
 * hardcodes a hex.
 */

export type Tone = 'positive' | 'negative' | 'caution' | 'info' | 'neutral';

type ToneVars = { fg: string; bg: string };

const TONE_VARS: Record<Tone, ToneVars> = {
  // Solid tone as the readable foreground on a low-alpha tint of itself.
  positive: { fg: 'var(--color-ok)', bg: 'var(--color-ok-subtle)' },
  negative: { fg: 'var(--color-bad)', bg: 'var(--color-bad-subtle)' },
  caution: { fg: 'var(--color-warn)', bg: 'var(--color-warn-subtle)' },
  info: { fg: 'var(--color-brand)', bg: 'var(--color-brand-subtle)' },
  neutral: { fg: 'var(--color-text-2)', bg: 'var(--color-surface-2)' }
};

/** CSS var for a tone's foreground (text/icon on its own subtle tint). */
export function toneFg(tone: Tone): string {
  return TONE_VARS[tone].fg;
}

/** CSS var for a tone's low-alpha background tint. */
export function toneBg(tone: Tone): string {
  return TONE_VARS[tone].bg;
}

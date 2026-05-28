/**
 * Single source of truth for span-kind visual treatment.
 *
 * A6 of FRONTEND_PRODUCT_PLAN.md: `llm_call` and `agent_turn` used to
 * render as identical dots in the tree, so the árbol was semantically
 * flat. Each kind gets a glyph + color here so SpanNode, future
 * trajectory waterfall (C2), and any other span surface share one
 * design language. Colors hook into the design tokens in app.css.
 *
 * Glyphs are Unicode (no SVG bundle) — the codebase has zero icon
 * conventions so far, and an inline char keeps the tree-row layout
 * trivial (no flex sizing/baseline tuning per kind).
 */

export type SpanKindStyle = {
  /** Compact glyph rendered before the span name. */
  glyph: string;
  /** CSS color token (var() reference) for the glyph + accents. */
  color: string;
  /** Short uppercase label used in dense badges. */
  label: string;
};

const FALLBACK: SpanKindStyle = {
  glyph: '·',
  color: 'var(--color-text-3)',
  label: 'span'
};

const KIND_STYLES: Record<string, SpanKindStyle> = {
  agent_turn: {
    glyph: '◆',
    color: 'var(--color-text-1)',
    label: 'agent'
  },
  llm_call: {
    glyph: '✦',
    color: 'var(--color-accent)',
    label: 'llm'
  },
  tool_call: {
    glyph: '⚙',
    color: 'var(--color-warning)',
    label: 'tool'
  },
  retrieval: {
    glyph: '◇',
    color: 'var(--color-chart-2)',
    label: 'retrieve'
  },
  memory_read: {
    glyph: '▽',
    color: 'var(--color-chart-2)',
    label: 'mem r'
  },
  memory_write: {
    glyph: '△',
    color: 'var(--color-chart-2)',
    label: 'mem w'
  },
  decision: {
    glyph: '◉',
    color: 'var(--color-success)',
    label: 'decide'
  },
  handoff: {
    glyph: '↦',
    color: 'var(--color-text-2)',
    label: 'handoff'
  },
  human_intervention: {
    glyph: '☞',
    color: 'var(--color-text-2)',
    label: 'human'
  },
  guardrail_check: {
    glyph: '◈',
    color: 'var(--color-success)',
    label: 'guard'
  },
  error: {
    glyph: '✕',
    color: 'var(--color-danger)',
    label: 'error'
  },
  custom: {
    glyph: '●',
    color: 'var(--color-text-3)',
    label: 'custom'
  }
};

export function styleForKind(kind: string): SpanKindStyle {
  return KIND_STYLES[kind] ?? FALLBACK;
}

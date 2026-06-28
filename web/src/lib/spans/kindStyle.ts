/**
 * Single source of truth for span-kind visual treatment.
 *
 * Each span kind gets a lucide icon + colour token + short label, so the span
 * tree, the detail header, and any future span surface share one design
 * language. An icon (not a Unicode glyph) reads crisp at small sizes and stays
 * on-grid with the rest of the Linear-style chrome. Colours hook into the
 * design tokens in app.css.
 */

import type { ComponentType } from 'svelte';
import {
  Bot,
  Sparkles,
  Wrench,
  Search,
  ArrowDownToLine,
  ArrowUpFromLine,
  GitBranch,
  CornerDownRight,
  Hand,
  ShieldCheck,
  AlertCircle,
  Circle,
  Dot
} from 'lucide-svelte';

export type SpanKindStyle = {
  /** Lucide icon rendered before the span name. */
  icon: ComponentType;
  /** CSS color token (var() reference) for the icon + accents. */
  color: string;
  /** Short uppercase label used in dense badges. */
  label: string;
};

const FALLBACK: SpanKindStyle = {
  icon: Dot,
  color: 'var(--color-text-3)',
  label: 'span'
};

const KIND_STYLES: Record<string, SpanKindStyle> = {
  agent_turn: {
    icon: Bot,
    color: 'var(--color-text-1)',
    label: 'agent'
  },
  llm_call: {
    icon: Sparkles,
    color: 'var(--color-brand)',
    label: 'llm'
  },
  tool_call: {
    icon: Wrench,
    color: 'var(--color-warn)',
    label: 'tool'
  },
  retrieval: {
    icon: Search,
    color: 'var(--color-chart-2)',
    label: 'retrieve'
  },
  memory_read: {
    icon: ArrowDownToLine,
    color: 'var(--color-chart-2)',
    label: 'mem r'
  },
  memory_write: {
    icon: ArrowUpFromLine,
    color: 'var(--color-chart-2)',
    label: 'mem w'
  },
  decision: {
    icon: GitBranch,
    color: 'var(--color-ok)',
    label: 'decide'
  },
  handoff: {
    icon: CornerDownRight,
    color: 'var(--color-text-2)',
    label: 'handoff'
  },
  human_intervention: {
    icon: Hand,
    color: 'var(--color-text-2)',
    label: 'human'
  },
  guardrail_check: {
    icon: ShieldCheck,
    color: 'var(--color-ok)',
    label: 'guard'
  },
  error: {
    icon: AlertCircle,
    color: 'var(--color-bad)',
    label: 'error'
  },
  custom: {
    icon: Circle,
    color: 'var(--color-text-3)',
    label: 'custom'
  }
};

export function styleForKind(kind: string): SpanKindStyle {
  return KIND_STYLES[kind] ?? FALLBACK;
}

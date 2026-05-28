/**
 * Compact facts surfaced beside a span name in the tree.
 *
 * A6 of FRONTEND_PRODUCT_PLAN.md: today the tree shows only
 * `name + duration_ms`. For a debugger, the high-value facts are
 * kind-specific (LLM: TTFT, tokens, $; tool: name + status; retrieval:
 * top_k). We compute them here as `{key, value}` pairs so the tree row
 * can render whichever fit, in priority order, and the span detail
 * pane can reuse the same projection without re-deriving.
 *
 * Every fact is optional — null/undefined inputs yield no fact. Order
 * in the returned array is render order (most informative first).
 */

import type { SpanSummary } from '$lib/api/client';

export type SpanFact = {
  /** Stable identifier for keying. */
  key: string;
  /** Already-formatted, ready-to-render string. */
  value: string;
  /** Optional title text (hover tooltip) — used for unit clarification. */
  title?: string;
};

function fmtTokens(n: number): string {
  if (n < 1000) return `${n} tok`;
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k tok`;
  return `${Math.round(n / 1000)}k tok`;
}

function fmtCost(usd: number): string {
  if (usd < 0.001) return `$${usd.toFixed(4)}`;
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function asBool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function factsFor(span: SpanSummary): SpanFact[] {
  const facts: SpanFact[] = [];
  const d = span.detail;
  switch (span.kind) {
    case 'llm_call': {
      const ttft = asNumber(d.time_to_first_token_ms);
      if (ttft !== null) {
        facts.push({ key: 'ttft', value: fmtMs(ttft), title: 'time to first token' });
      }
      const tokens = d.tokens as { total?: number } | undefined;
      const total = asNumber(tokens?.total);
      if (total !== null && total > 0) {
        facts.push({ key: 'tokens', value: fmtTokens(total), title: 'total tokens' });
      }
      const cost = d.cost_usd as { total?: number } | undefined;
      const costTotal = asNumber(cost?.total);
      if (costTotal !== null && costTotal > 0) {
        facts.push({ key: 'cost', value: fmtCost(costTotal), title: 'cost (USD)' });
      }
      const cacheHit = asBool(d.cache_hit);
      if (cacheHit) {
        facts.push({ key: 'cache', value: 'cache', title: 'served from prompt cache' });
      }
      const retries = asNumber(d.retries);
      if (retries !== null && retries > 0) {
        facts.push({ key: 'retries', value: `${retries}×retry`, title: 'retry count' });
      }
      break;
    }
    case 'tool_call': {
      const name = asString(d.tool_name);
      if (name) facts.push({ key: 'tool', value: name, title: 'tool name' });
      const status = asString(d.status);
      // Show status only when it's not the boring success case — the
      // tree should highlight failures, not echo "ok" on every row.
      if (status && status !== 'ok') {
        facts.push({ key: 'status', value: status, title: 'tool call status' });
      }
      break;
    }
    case 'retrieval': {
      const retriever = asString(d.retriever);
      if (retriever) facts.push({ key: 'retriever', value: retriever });
      const returned = asNumber(d.top_k_returned);
      const requested = asNumber(d.top_k_requested);
      if (returned !== null && requested !== null) {
        facts.push({
          key: 'k',
          value: `${returned}/${requested}`,
          title: 'top_k returned / requested'
        });
      }
      break;
    }
    case 'memory_read':
    case 'memory_write': {
      const store = asString(d.memory_store);
      if (store) facts.push({ key: 'store', value: store, title: 'memory store' });
      break;
    }
    case 'decision': {
      const chosen = asString(d.chosen);
      if (chosen) facts.push({ key: 'chosen', value: chosen, title: 'decision chosen' });
      break;
    }
    case 'guardrail_check': {
      const guardrail = asString(d.guardrail);
      if (guardrail) facts.push({ key: 'guardrail', value: guardrail });
      const passed = asBool(d.passed);
      if (passed !== null) {
        facts.push({ key: 'passed', value: passed ? 'pass' : 'fail' });
      }
      break;
    }
    case 'error': {
      const errorType = asString(d.error_type);
      if (errorType) facts.push({ key: 'type', value: errorType, title: 'error type' });
      const recoverable = asBool(d.recoverable);
      if (recoverable !== null) {
        facts.push({ key: 'recover', value: recoverable ? 'recovered' : 'fatal' });
      }
      break;
    }
  }
  return facts;
}

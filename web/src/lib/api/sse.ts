/**
 * Browser EventSource helper for /api/.../traces/{run_id}/stream.
 *
 * Three event kinds matter to callers: `snapshot`, `span`, `complete`.
 * Heartbeats (`ping`) are absorbed silently.
 */

import type { SpanSummary, TraceDetail } from '$lib/api/client';

export type StreamHandlers = {
  onSnapshot?: (trace: TraceDetail) => void;
  onSpan?: (span: SpanSummary) => void;
  onComplete?: (finalState: string) => void;
  onError?: (err: Event) => void;
};

export type StreamHandle = {
  close: () => void;
};

export function openTraceStream(
  workspaceId: string,
  runId: string,
  handlers: StreamHandlers
): StreamHandle {
  const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/traces/${encodeURIComponent(runId)}/stream`;
  const es = new EventSource(url);
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    es.close();
  };

  es.addEventListener('snapshot', (e) => {
    if (closed || !handlers.onSnapshot) return;
    try {
      handlers.onSnapshot(JSON.parse((e as MessageEvent).data));
    } catch {
      /* malformed snapshot — ignore, keep listening */
    }
  });

  es.addEventListener('span', (e) => {
    if (closed || !handlers.onSpan) return;
    try {
      handlers.onSpan(JSON.parse((e as MessageEvent).data));
    } catch {
      /* malformed span */
    }
  });

  es.addEventListener('complete', (e) => {
    if (closed) return;
    let finalState = 'completed';
    try {
      finalState = JSON.parse((e as MessageEvent).data).final_state ?? 'completed';
    } catch {
      /* keep default */
    }
    handlers.onComplete?.(finalState);
    close();
  });

  es.onerror = (e) => {
    if (closed) return;
    handlers.onError?.(e);
    // The browser auto-reconnects on transient errors; we only close
    // explicitly on `complete` or external request.
  };

  return { close };
}

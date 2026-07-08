import { ApiError, authHeaders, request } from '$lib/api/http';
import type { PromoteCaseDraft, ThreadDetail, TraceDetail } from '$lib/api/types';

export const tracesApi = {
  trace: (workspaceId: string, traceId: string, fetch?: typeof globalThis.fetch) =>
    request<TraceDetail>(`/api/workspaces/${workspaceId}/traces/${traceId}`, {
      fetch
    }),

  /**
   * Resolve a `*_pointer` field from a span detail. The API returns the
   * raw bytes; the caller decides how to render (JSON, markdown, plain).
   * Throws `ApiError` (400/404/500) on failure — the trace viewer
   * surfaces those inline so a missing payload doesn't break the page.
   */
  resolvePayload: async (
    workspaceId: string,
    pointer: string,
    fetch?: typeof globalThis.fetch
  ): Promise<{ text: string; isJson: boolean }> => {
    const f = fetch ?? globalThis.fetch;
    const url = `/api/workspaces/${workspaceId}/payloads?pointer=${encodeURIComponent(pointer)}`;
    const res = await f(url, {
      headers: authHeaders()
    });
    if (!res.ok) {
      // Reuse the same body-reading discipline as request() — read once
      // as text, then try to parse. See http.ts request() for the
      // history of the "Body has already been read" bug.
      const raw = await res.text();
      let body: unknown = raw;
      try {
        body = JSON.parse(raw);
      } catch {
        // not JSON
      }
      throw new ApiError(res.status, body);
    }
    const text = await res.text();
    const isJson = res.headers.get('content-type')?.startsWith('application/json') ?? false;
    return { text, isJson };
  },

  thread: (workspaceId: string, threadId: string, fetch?: typeof globalThis.fetch) =>
    request<ThreadDetail>(`/api/workspaces/${workspaceId}/threads/${threadId}`, {
      fetch
    }),

  draftCaseFromTrace: (
    workspaceId: string,
    traceId: string,
    body: { name?: string; notes?: string } = {},
    fetch?: typeof globalThis.fetch
  ) =>
    request<PromoteCaseDraft>(`/api/workspaces/${workspaceId}/traces/${traceId}/case-draft`, {
      method: 'POST',
      json: body,
      fetch
    })
};

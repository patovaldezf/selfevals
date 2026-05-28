/**
 * Server-side hooks.
 *
 * BUG-4 (docs/FE_FASE_A_PENDIENTES.md): when `selfevals serve` launches
 * the SvelteKit `adapter-node` build alongside FastAPI, the Node server
 * has no route for `/api/*` and every page that calls
 * `fetch('/api/...')` in its `+page.server.ts` gets a 404 — both during
 * SSR and from the browser. Vite dev server has a proxy config, but the
 * production build doesn't.
 *
 * Fix: when `SELFEVALS_API_BASE` is set (cmd_serve sets it to the
 * FastAPI URL), intercept every `/api/*` request, forward it to that
 * origin, and stream the upstream response straight back to the caller.
 * This is the absolute-minimum proxy — no rewriting, no auth injection,
 * no body buffering — because the API and the web are explicitly
 * peers on the same machine and the only thing we're papering over is
 * "different port number".
 *
 * SSE matters here: `/api/workspaces/{ws}/runs/{run}/stream` returns a
 * never-ending text/event-stream. We must not buffer the body (would
 * never flush) and we must keep `cache-control: no-cache, no-transform`
 * intact (already on the upstream response). `fetch` + passing
 * `response.body` through works for both SSE and regular responses
 * because undici streams the body lazily.
 */

import type { Handle } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';

const HOP_BY_HOP_HEADERS = new Set([
  // Per RFC 7230 §6.1 — these are connection-scoped and must not be
  // forwarded by a proxy. Most matter little here but `connection`
  // and `transfer-encoding` can confuse downstream undici versions.
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade'
]);

function copyHeaders(src: Headers): Headers {
  const out = new Headers();
  src.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      out.set(key, value);
    }
  });
  return out;
}

export const handle: Handle = async ({ event, resolve }) => {
  const apiBase = env.SELFEVALS_API_BASE;
  if (apiBase && event.url.pathname.startsWith('/api/')) {
    // Build the upstream URL: keep path + query exactly as the caller
    // sent them, swap only the origin.
    const upstreamUrl = apiBase.replace(/\/$/, '') + event.url.pathname + event.url.search;

    const init: RequestInit = {
      method: event.request.method,
      headers: copyHeaders(event.request.headers),
      // SSE is GET-only; for POST/PUT/PATCH/DELETE we need to forward
      // the body. `event.request.body` is a ReadableStream; undici
      // requires `duplex: 'half'` when streaming a request body.
      body: ['GET', 'HEAD'].includes(event.request.method) ? undefined : event.request.body,
      // @ts-expect-error — `duplex` is part of the WHATWG fetch spec but not
      // yet in lib.dom.d.ts. Undici (Node's runtime fetch) requires it
      // whenever you pass a streaming body.
      duplex: 'half'
    };

    let upstream: Response;
    try {
      upstream = await fetch(upstreamUrl, init);
    } catch (err) {
      // Network-level failure (FastAPI down, DNS, refused connection).
      // Return a 502 with a body the FE's API client can JSON-parse so
      // the "Backend unreachable" affordance keeps working.
      const message = err instanceof Error ? err.message : String(err);
      return new Response(
        JSON.stringify({ detail: `selfevals proxy: upstream fetch failed: ${message}` }),
        { status: 502, headers: { 'content-type': 'application/json' } }
      );
    }

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: copyHeaders(upstream.headers)
    });
  }

  return resolve(event);
};

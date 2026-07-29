/**
 * Single request/auth/error layer shared by every `api/resources/*` module.
 */

const DEFAULT_BASE = '';

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    super(`API ${status}`);
    this.status = status;
    this.body = body;
  }

  /** A human-facing message: FastAPI's `{detail}` if present, else the status. */
  get detail(): string {
    const b = this.body;
    if (b && typeof b === 'object' && 'detail' in b) {
      const d = (b as { detail: unknown }).detail;
      if (typeof d === 'string') return d;
    }
    if (typeof b === 'string' && b.trim()) return b;
    return `Request failed (${this.status})`;
  }
}

export type RequestInitX = Omit<RequestInit, 'body'> & {
  fetch?: typeof fetch;
  /** JSON body — serialized and sent with `Content-Type: application/json`. */
  json?: unknown;
  /** Multipart body — sent as-is; the browser owns the `Content-Type` boundary. */
  form?: FormData;
};

/**
 * Identity headers for an API call.
 *
 * The session cookie is the real credential: it is `HttpOnly`, so this code
 * cannot read it, and the browser attaches it automatically once `credentials`
 * is set on the request (below). The `X-SelfEvals-User` header remains for
 * `SELFEVALS_AUTH_MODE=local`, where there is no login and the API expects a
 * caller string — the server ignores it whenever a valid session exists, so
 * sending both is safe and keeps local development working with no setup.
 */
export function authHeaders(extra?: HeadersInit): Record<string, string> {
  return {
    'X-SelfEvals-User': 'local',
    ...((extra as Record<string, string>) ?? {})
  };
}

export async function request<T>(path: string, init?: RequestInitX): Promise<T> {
  const f = init?.fetch ?? fetch;
  const { fetch: _f, json, form, headers, ...rest } = init ?? {};

  // A bare GET keeps the original `X-SelfEvals-User` + JSON content type. A
  // mutation either serializes `json` (JSON content type) or passes `form`
  // through *without* setting Content-Type so the browser appends the
  // multipart boundary itself — setting it by hand corrupts the upload.
  const mergedHeaders = authHeaders({
    ...(form ? {} : { 'Content-Type': 'application/json' }),
    ...((headers as Record<string, string>) ?? {})
  });

  const res = await f(DEFAULT_BASE + path, {
    ...rest,
    // Send the HttpOnly session cookie. `fetch` omits credentials by default,
    // so without this every request would arrive unauthenticated once login is
    // enabled — and the failure is silent, because the request still succeeds
    // in `local` mode where nothing is enforced.
    credentials: 'same-origin',
    headers: mergedHeaders,
    body: form ?? (json !== undefined ? JSON.stringify(json) : undefined)
  });
  if (!res.ok) {
    // Read the body once as text, then try JSON. Calling `res.json()` then
    // `res.text()` in a fallback consumes the body on the first call and
    // the second throws `TypeError: Body is unusable: Body has already
    // been read` — which masks the real upstream error with a confusing
    // one. Text-first lets us preserve the original body either way.
    const raw = await res.text();
    let body: unknown = raw;
    try {
      body = JSON.parse(raw);
    } catch {
      // not JSON; keep raw text
    }
    throw new ApiError(res.status, body);
  }
  // 204 / empty body (some mutations ack without content).
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Build a `?from=&to=&...` query string, dropping undefined values. */
export function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

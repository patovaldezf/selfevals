<!--
  Lazy-resolve an object-store pointer (`oss://<ws>/sha256:<hex>`).

  Pointers live on span detail fields like `system_prompt_pointer`,
  `messages_pointer`, `args_pointer`, `result_pointer`, etc. The actual
  bytes live in the filesystem object store and are NOT inlined into
  the trace JSON (could be huge, would dominate trace size). This
  component shows the pointer truncated, lets the user click to resolve,
  and renders the result inline — JSON when the server says it's JSON,
  preformatted text otherwise — with a one-click copy.

  Resolved payloads are content-addressed (sha256), so the same pointer
  always yields the same bytes. We cache by pointer string across the
  whole session, so re-opening a span you already looked at is instant.
-->
<script lang="ts" context="module">
  // Module-level cache: pointer string -> resolved payload. Content-addressed,
  // so this never goes stale within a session.
  const payloadCache = new Map<string, { text: string; isJson: boolean }>();
</script>

<script lang="ts">
  import { page } from '$app/stores';
  import { api, ApiError } from '$lib/api/client';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { Copy, Check, ChevronDown } from 'lucide-svelte';

  export let label: string;
  /** The pointer string, or null if the span doesn't have this field. */
  export let pointer: string | null;
  /** Optional content hash for display (verifies integrity at resolve time on the server). */
  export let hash: string | null = null;

  type State =
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'resolved'; text: string; isJson: boolean }
    | { kind: 'error'; status: number; message: string };

  let state: State = { kind: 'idle' };
  let copied = false;

  $: shortPointer = pointer ? truncatePointer(pointer) : null;

  function truncatePointer(p: string): string {
    // oss://<ws>/sha256:<64hex> — show ws + first 12 of the hash so it's
    // identifiable but doesn't dominate the layout.
    const m = p.match(/^oss:\/\/([^/]+)\/sha256:([0-9a-f]+)$/);
    if (!m) return p;
    return `oss://${m[1]}/sha256:${m[2].slice(0, 12)}…`;
  }

  async function resolve() {
    if (!pointer) return;
    const cached = payloadCache.get(pointer);
    if (cached) {
      state = { kind: 'resolved', text: cached.text, isJson: cached.isJson };
      return;
    }
    const ws = $page.params.workspace;
    if (!ws) return;
    state = { kind: 'loading' };
    try {
      const result = await api.resolvePayload(ws, pointer);
      payloadCache.set(pointer, { text: result.text, isJson: result.isJson });
      state = { kind: 'resolved', text: result.text, isJson: result.isJson };
    } catch (e) {
      if (e instanceof ApiError) {
        const msg =
          typeof e.body === 'object' && e.body && 'detail' in e.body
            ? String((e.body as { detail: unknown }).detail)
            : `${e.status}`;
        state = { kind: 'error', status: e.status, message: msg };
      } else {
        state = {
          kind: 'error',
          status: 0,
          message: e instanceof Error ? e.message : 'unknown error'
        };
      }
    }
  }

  function collapse() {
    state = { kind: 'idle' };
  }

  function prettyIfJson(text: string, isJson: boolean): string {
    if (!isJson) return text;
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return text;
    }
  }

  async function copy() {
    if (state.kind !== 'resolved') return;
    try {
      await navigator.clipboard.writeText(prettyIfJson(state.text, state.isJson));
      copied = true;
      setTimeout(() => (copied = false), 1400);
    } catch {
      /* clipboard blocked — no-op, the payload is still visible to select */
    }
  }
</script>

{#if pointer}
  <div class="pf">
    <div class="pf-head">
      <span class="pf-label">{label}</span>
      <div class="pf-actions">
        {#if state.kind === 'resolved'}
          <button type="button" class="pf-btn" on:click={copy} title="Copy payload">
            <Icon icon={copied ? Check : Copy} size={13} />
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
          <button type="button" class="pf-btn" on:click={collapse}>Collapse</button>
        {:else if state.kind !== 'loading'}
          <button type="button" class="pf-btn pf-btn-go" on:click={resolve}>
            {state.kind === 'error' ? 'Retry' : 'Resolve'}
            <Icon icon={ChevronDown} size={13} />
          </button>
        {/if}
      </div>
    </div>

    <div class="pf-pointer" title={pointer}>
      {shortPointer}{#if hash}<span class="pf-hash"> · {hash.slice(0, 19)}…</span>{/if}
    </div>

    {#if state.kind === 'loading'}
      <div class="pf-loading">resolving…</div>
    {:else if state.kind === 'resolved'}
      <pre class="pf-payload">{prettyIfJson(state.text, state.isJson)}</pre>
    {:else if state.kind === 'error'}
      <div class="pf-error">{state.status} · {state.message}</div>
    {/if}
  </div>
{:else}
  <div class="pf-empty">
    <span class="pf-label">{label}</span>
    <span class="pf-empty-note">not captured</span>
  </div>
{/if}

<style>
  .pf {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .pf-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .pf-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .pf-actions {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  .pf-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .pf-btn:hover {
    color: var(--color-text-1);
  }
  .pf-btn-go {
    color: var(--color-brand-strong);
  }
  .pf-pointer {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pf-hash {
    color: var(--color-text-3);
  }
  .pf-loading {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    font-style: italic;
  }
  .pf-payload {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    line-height: 1.55;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 0.75rem;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 24rem;
    color: var(--color-text-1);
  }
  .pf-error {
    font-size: var(--text-xs);
    color: var(--color-bad);
    font-family: var(--font-mono);
  }
  .pf-empty {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .pf-empty-note {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    font-style: italic;
  }
</style>

<!--
  Lazy-resolve an object-store pointer (`oss://<ws>/sha256:<hex>`).

  Pointers live on span detail fields like `system_prompt_pointer`,
  `messages_pointer`, `args_pointer`, `result_pointer`, etc. The actual
  bytes live in the filesystem object store and are NOT inlined into
  the trace JSON (could be huge, would dominate trace size). This
  component shows the pointer truncated, lets the user click to resolve,
  and renders the result inline — JSON when the server says it's JSON,
  preformatted text otherwise.

  Karpathy filter (FRONTEND_PRODUCT_PLAN.md §3): without this, the trace
  viewer is theater — every prompt/output is `null` or an opaque pointer
  string. With this, the real prompt is one click away.
-->
<script lang="ts">
  import { page } from '$app/stores';
  import { api, ApiError } from '$lib/api/client';

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
    const ws = $page.params.workspace;
    if (!ws) return;
    state = { kind: 'loading' };
    try {
      const result = await api.resolvePayload(ws, pointer);
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
</script>

{#if pointer}
  <div class="space-y-1.5">
    <div class="flex items-baseline justify-between gap-3">
      <div class="text-xs uppercase tracking-wide text-text-3">{label}</div>
      {#if state.kind === 'resolved'}
        <button
          type="button"
          on:click={collapse}
          class="text-xs text-text-3 hover:text-text-1 transition-colors"
        >
          Collapse
        </button>
      {:else if state.kind !== 'loading'}
        <button
          type="button"
          on:click={resolve}
          class="text-xs text-text-2 hover:text-text-1 transition-colors flex items-center gap-1"
        >
          {state.kind === 'error' ? 'Retry' : 'Resolve'} <span aria-hidden="true">↓</span>
        </button>
      {/if}
    </div>
    <div class="font-mono text-[11px] text-text-3 truncate" title={pointer}>
      {shortPointer}{#if hash}<span class="text-text-3"> · {hash.slice(0, 19)}…</span>{/if}
    </div>
    {#if state.kind === 'loading'}
      <div class="text-xs text-text-3 italic">resolving…</div>
    {:else if state.kind === 'resolved'}
      <pre
        class="font-mono text-xs bg-surface-2 border border-border rounded p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-96">{prettyIfJson(
          state.text,
          state.isJson
        )}</pre>
    {:else if state.kind === 'error'}
      <div class="text-xs text-danger font-mono">
        {state.status} · {state.message}
      </div>
    {/if}
  </div>
{:else}
  <div>
    <div class="text-xs uppercase tracking-wide text-text-3">{label}</div>
    <div class="text-xs text-text-3 italic">not captured</div>
  </div>
{/if}

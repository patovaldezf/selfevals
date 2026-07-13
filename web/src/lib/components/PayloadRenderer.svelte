<!--
  PayloadRenderer: the "Payloads" section of a span's detail pane.

  Payload fields by span kind. The schema (schemas/trace.py) puts these on
  every span as `<name>_pointer` + `<name>_hash`, and (schema 1.3.0/1.4.0)
  a `<name>_inline` for small payloads. We prefer the inline copy — it renders
  immediately without resolving a pointer (LangSmith-parity: inputs/outputs
  visible at a glance, no "resolve" click for the common case). When a payload
  is large enough to have been offloaded, only the pointer is present and we fall back
  to a PointerField the user clicks to resolve.

  Message-shaped payloads (messages, system prompt, output content) render
  through MessageThread so roles are differentiated and multi-line prompts
  read as written; everything else is shown as pretty JSON / preformatted text.

  Purely presentational and depends only on `selected` — the cleanest cut of
  the trace page.
-->
<script lang="ts">
  import PointerField from '$lib/components/PointerField.svelte';
  import MessageThread from '$lib/components/MessageThread.svelte';
  import AudioPlayer from '$lib/components/AudioPlayer.svelte';
  import type { SpanSummary } from '$lib/api/client';

  let { selected }: { selected: SpanSummary } = $props();

  type FieldSpec = { label: string; field: string; thread?: boolean };

  const FIELDS_BY_KIND: Record<string, FieldSpec[]> = {
    llm_call: [
      { label: 'system prompt', field: 'system_prompt' },
      { label: 'messages', field: 'messages', thread: true },
      { label: 'output content', field: 'output.content' },
      { label: 'reasoning summary', field: 'reasoning.summary' },
      { label: 'reasoning full', field: 'reasoning.full' }
    ],
    tool_call: [
      { label: 'args', field: 'args' },
      { label: 'result', field: 'result' }
    ],
    retrieval: [{ label: 'query', field: 'query' }],
    memory_read: [{ label: 'values', field: 'values' }],
    memory_write: [{ label: 'values', field: 'values' }],
    stt: [{ label: 'transcript', field: 'transcript' }],
    tts: [{ label: 'text', field: 'text' }]
  };

  // Audio clip pointer for voice spans, resolved as an <audio> player.
  const audioPointer = $derived(
    selected.kind === 'stt' || selected.kind === 'tts'
      ? {
          pointer:
            typeof selected.detail.audio_pointer === 'string'
              ? selected.detail.audio_pointer
              : null,
          mime:
            typeof selected.detail.audio_mime_type === 'string'
              ? selected.detail.audio_mime_type
              : null
        }
      : null
  );

  type Resolved = {
    label: string;
    field: string;
    thread: boolean;
    pointer: string | null;
    hash: string | null;
    inline: string | null;
  };

  /** Navigate a dotted path on the span detail and read pointer/hash/inline. */
  function readField(detail: Record<string, unknown>, dottedField: string): {
    pointer: string | null;
    hash: string | null;
    inline: string | null;
  } {
    const segments = dottedField.split('.');
    const leaf = segments.pop() as string;
    let obj: Record<string, unknown> = detail;
    for (const seg of segments) {
      const next = obj[seg];
      if (!next || typeof next !== 'object') return { pointer: null, hash: null, inline: null };
      obj = next as Record<string, unknown>;
    }
    const pointer = obj[`${leaf}_pointer`];
    const hash = obj[`${leaf}_hash`];
    const inline = obj[`${leaf}_inline`];
    return {
      pointer: typeof pointer === 'string' ? pointer : null,
      hash: typeof hash === 'string' ? hash : null,
      inline: typeof inline === 'string' ? inline : null
    };
  }

  function prettyJson(text: string): string {
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return text;
    }
  }

  const fields = $derived<Resolved[]>(
    (FIELDS_BY_KIND[selected.kind] ?? []).map((f) => ({
      label: f.label,
      field: f.field,
      thread: f.thread ?? false,
      ...readField(selected.detail as Record<string, unknown>, f.field)
    }))
  );
  // A field is worth showing if it has *any* payload — inline (render now) or a
  // pointer (resolve on click). Audio clips count too.
  const hasAny = $derived(
    fields.some((f) => f.inline !== null || f.pointer !== null) ||
      audioPointer?.pointer != null
  );
</script>

{#if fields.length > 0}
  <section class="payloads">
    <div class="payloads-head">
      <span>Payloads</span>
      {#if !hasAny}
        <span class="payloads-none">none captured for this span</span>
      {/if}
    </div>
    {#if hasAny}
      <div class="payloads-list">
        {#each fields as f (f.field)}
          {#if f.inline !== null}
            <div class="field">
              <div class="field-head">
                <span class="field-label">{f.label}</span>
                {#if f.pointer}
                  <span class="field-truncated" title="Full payload offloaded — resolve below">
                    preview
                  </span>
                {/if}
              </div>
              {#if f.thread}
                <MessageThread text={f.inline} />
              {:else}
                <pre class="field-payload">{prettyJson(f.inline)}</pre>
              {/if}
              {#if f.pointer}
                <!-- Inline was truncated; let the user resolve the full bytes. -->
                <PointerField label={`${f.label} (full)`} pointer={f.pointer} hash={f.hash} />
              {/if}
            </div>
          {:else if f.pointer}
            <PointerField label={f.label} pointer={f.pointer} hash={f.hash} />
          {/if}
        {/each}
        {#if audioPointer?.pointer}
          <AudioPlayer
            label="audio"
            pointer={audioPointer.pointer}
            mimeType={audioPointer.mime}
          />
        {/if}
      </div>
    {/if}
  </section>
{/if}

<style>
  .payloads {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    padding: 1rem 1.2rem;
    margin-bottom: 1.5rem;
  }
  .payloads-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.9rem;
  }
  .payloads-none {
    text-transform: none;
    font-style: italic;
  }
  .payloads-list {
    display: flex;
    flex-direction: column;
    gap: 1.1rem;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .field-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .field-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .field-truncated {
    font-size: var(--text-2xs);
    font-style: italic;
    color: var(--color-text-3);
  }
  .field-payload {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    line-height: 1.55;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 0.65rem 0.75rem;
    margin: 0;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 24rem;
    color: var(--color-text-1);
  }
</style>

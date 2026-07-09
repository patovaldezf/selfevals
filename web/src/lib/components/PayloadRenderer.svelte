<!--
  PayloadRenderer: the "Payloads" section of a span's detail pane.

  Pointer fields by span kind. The schema (schemas/trace.py) puts these on
  every span as `<name>_pointer` + `<name>_hash`. We surface them as
  PointerField widgets so the user can click to resolve the actual bytes —
  without this, every `*_pointer: "oss://..."` in the JSON dump is opaque and
  the trace viewer is debug theater.

  Purely presentational and depends only on `selected` — the cleanest cut of
  the trace page.
-->
<script lang="ts">
  import PointerField from '$lib/components/PointerField.svelte';
  import type { SpanSummary } from '$lib/api/client';

  export let selected: SpanSummary;

  const POINTERS_BY_KIND: Record<string, Array<{ label: string; field: string }>> = {
    llm_call: [
      { label: 'system prompt', field: 'system_prompt' },
      { label: 'messages', field: 'messages' },
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
    memory_write: [{ label: 'values', field: 'values' }]
  };

  /** Navigate a dotted path on the span detail and return `<field>_pointer` + `<field>_hash`. */
  function readPointer(
    detail: Record<string, unknown>,
    dottedField: string
  ): { pointer: string | null; hash: string | null } {
    const segments = dottedField.split('.');
    const leaf = segments.pop() as string;
    let obj: Record<string, unknown> = detail;
    for (const seg of segments) {
      const next = obj[seg];
      if (!next || typeof next !== 'object') return { pointer: null, hash: null };
      obj = next as Record<string, unknown>;
    }
    const pointer = obj[`${leaf}_pointer`];
    const hash = obj[`${leaf}_hash`];
    return {
      pointer: typeof pointer === 'string' ? pointer : null,
      hash: typeof hash === 'string' ? hash : null
    };
  }

  $: pointerFields = POINTERS_BY_KIND[selected.kind]
    ? POINTERS_BY_KIND[selected.kind].map((p) => ({
        ...p,
        ...readPointer(selected.detail, p.field)
      }))
    : [];
  $: hasAnyPointer = pointerFields.some((p) => p.pointer !== null);
</script>

{#if pointerFields.length > 0}
  <section class="payloads">
    <div class="payloads-head">
      <span>Payloads</span>
      {#if !hasAnyPointer}
        <span class="payloads-none">none captured for this span</span>
      {/if}
    </div>
    {#if hasAnyPointer}
      <div class="payloads-list">
        {#each pointerFields as f (f.field)}
          {#if f.pointer}
            <PointerField label={f.label} pointer={f.pointer} hash={f.hash} />
          {/if}
        {/each}
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
</style>

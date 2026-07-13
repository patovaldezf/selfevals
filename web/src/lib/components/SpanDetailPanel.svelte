<!--
  SpanDetailPanel: the right-hand detail pane of the trace viewer.

  Purely presentational — renders the selected span's facets, error banner,
  payloads (via PayloadRenderer), and raw JSON, or the trace-failed banner
  when nothing is selected yet.
-->
<script lang="ts">
  import Icon from '$lib/components/ui/Icon.svelte';
  import PayloadRenderer from '$lib/components/PayloadRenderer.svelte';
  import SpanReplayPanel from '$lib/components/SpanReplayPanel.svelte';
  import { factsFor } from '$lib/spans/facts';
  import { styleForKind } from '$lib/spans/kindStyle';
  import type { SpanSummary } from '$lib/api/client';
  import { ArrowRight, AlertCircle } from 'lucide-svelte';

  export let selected: SpanSummary | null;
  export let traceFailed: boolean;
  export let finalState: string;
  export let workspaceId: string = '';
  export let traceId: string = '';
  export let live: boolean = false;

  type LLMOutputLike = { stop_reason?: string };

  /** The span's answer text, for the Playground's original-vs-alternate diff. */
  function outputContent(s: SpanSummary): string | null {
    const output = s.detail.output as { content_inline?: string } | undefined;
    return typeof output?.content_inline === 'string' ? output.content_inline : null;
  }

  function llmFacets(s: SpanSummary) {
    const output = s.detail.output as LLMOutputLike | undefined;
    return [
      { label: 'provider', value: s.detail.provider as string | undefined },
      { label: 'model', value: s.detail.model as string | undefined },
      { label: 'stop reason', value: output?.stop_reason }
    ];
  }

  // Is a span a failure? Mirrors the tree's logic so the detail pane can flag it.
  function spanError(s: SpanSummary): string | null {
    if (typeof s.detail.error === 'string') return s.detail.error;
    const fs = s.detail.final_state as { status?: string; error?: string } | undefined;
    if (fs?.error) return fs.error;
    if (s.kind === 'error') return (s.detail.message as string | undefined) ?? 'error';
    return null;
  }
</script>

{#if traceFailed && !selected}
  <!-- The trace failed: lead with that before the user picks a span. -->
  <div class="trace-error">
    <Icon icon={AlertCircle} size={16} />
    <div>
      <div class="trace-error-title">This run ended in <strong>{finalState}</strong></div>
      <div class="trace-error-sub">Open the red spans in the tree to see where it broke.</div>
    </div>
  </div>
{/if}

{#if selected}
  {@const selectedStyle = styleForKind(selected.kind)}
  {@const selectedFacts = factsFor(selected)}
  {@const err = spanError(selected)}
  <div class="detail-eyebrow">
    <span class="detail-kind" style:color={selectedStyle.color}>
      <Icon icon={selectedStyle.icon} size={14} strokeWidth={2} />
      <span class="detail-kind-label">{selectedStyle.label}</span>
    </span>
    <span aria-hidden="true">·</span>
    <span class="mono" data-numeric>{selected.duration_ms}ms</span>
    {#each selectedFacts as f (f.key)}
      <span aria-hidden="true">·</span>
      <span class="mono" data-numeric title={f.title ?? f.key}>{f.value}</span>
    {/each}
  </div>
  <h2 class="detail-name">{selected.name}</h2>

  {#if err}
    <div class="span-error">
      <Icon icon={AlertCircle} size={15} />
      <span class="span-error-text">{err}</span>
    </div>
  {/if}

  {#if selected.kind === 'llm_call'}
    <section class="facets">
      {#each llmFacets(selected) as item}
        <div class="facet">
          <div class="facet-label">{item.label}</div>
          <div class="facet-value mono">{item.value ?? '—'}</div>
        </div>
      {/each}
    </section>
  {/if}

  <PayloadRenderer {selected} />

  {#if selected.kind === 'llm_call' && !live && workspaceId && traceId}
    <SpanReplayPanel
      {workspaceId}
      {traceId}
      spanId={selected.id}
      originalContent={outputContent(selected)}
    />
  {/if}

  <details class="raw">
    <summary>
      <Icon icon={ArrowRight} size={12} />
      Raw detail
    </summary>
    <pre class="raw-pre">{JSON.stringify(selected.detail, null, 2)}</pre>
  </details>
{:else if !traceFailed}
  <div class="empty">Select a span to inspect.</div>
{/if}

<style>
  .trace-error {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    padding: 0.9rem 1.1rem;
    border: 1px solid color-mix(in srgb, var(--color-bad) 30%, var(--color-border));
    border-radius: var(--radius-lg);
    background: var(--color-bad-subtle);
    color: var(--color-bad);
    margin-bottom: 1.5rem;
  }
  .trace-error-title {
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .trace-error-sub {
    font-size: var(--text-xs);
    color: var(--color-text-2);
    margin-top: 0.15rem;
  }
  .detail-eyebrow {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 0.4rem;
  }
  .detail-kind {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .detail-kind-label {
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .detail-name {
    font-size: var(--text-xl);
    font-weight: 600;
    margin-bottom: 1.5rem;
    word-break: break-word;
  }
  .span-error {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.7rem 0.9rem;
    border: 1px solid color-mix(in srgb, var(--color-bad) 30%, var(--color-border));
    border-radius: var(--radius-md);
    background: var(--color-bad-subtle);
    color: var(--color-bad);
    margin-bottom: 1.5rem;
  }
  .span-error-text {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    word-break: break-word;
  }
  .facets {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1rem;
    margin-bottom: 1.5rem;
  }
  .facet {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    padding: 0.75rem 1rem;
  }
  .facet-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.25rem;
  }
  .facet-value {
    font-size: var(--text-sm);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .raw summary {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    cursor: pointer;
    list-style: none;
    margin-bottom: 0.5rem;
  }
  .raw summary:hover {
    color: var(--color-text-1);
  }
  .raw summary :global(svg) {
    transition: transform var(--dur-fast) var(--ease-out);
  }
  .raw[open] summary :global(svg) {
    transform: rotate(90deg);
  }
  .raw-pre {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    padding: 1.25rem;
    overflow-x: auto;
  }
  .empty {
    color: var(--color-text-3);
    font-size: var(--text-sm);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>

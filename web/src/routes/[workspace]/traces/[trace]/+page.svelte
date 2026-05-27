<script lang="ts">
  import SpanNode from '$lib/components/SpanNode.svelte';
  import type { SpanSummary } from '$lib/api/client';
  import { openTraceStream, type StreamHandle } from '$lib/api/sse';
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import type { PageData } from './$types';

  export let data: PageData;

  let selected: SpanSummary | null = null;

  // Live state: starts from the server-loaded snapshot and is augmented
  // by SSE events. We don't mutate `data.trace` directly so refetches
  // remain clean.
  let spans: SpanSummary[] = [...data.trace.spans];
  let finalState = data.trace.final_state;
  let live = false;
  let streamHandle: StreamHandle | null = null;

  type LLMOutputLike = { stop_reason?: string };

  function llmFacets(s: SpanSummary) {
    const output = s.detail.output as LLMOutputLike | undefined;
    return [
      { label: 'provider', value: s.detail.provider as string | undefined },
      { label: 'model', value: s.detail.model as string | undefined },
      { label: 'stop reason', value: output?.stop_reason }
    ];
  }

  $: tree = (() => {
    const byParent = new Map<string | null, SpanSummary[]>();
    for (const s of spans) {
      const list = byParent.get(s.parent_id) ?? [];
      list.push(s);
      byParent.set(s.parent_id, list);
    }
    for (const list of byParent.values()) {
      list.sort(
        (a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime()
      );
    }
    return byParent;
  })();

  onMount(() => {
    const workspaceId = $page.params.workspace;
    const runId = data.trace.run_id;
    if (!workspaceId || !runId) return;
    live = true;
    streamHandle = openTraceStream(workspaceId, runId, {
      onSnapshot: (trace) => {
        // Snapshot replaces our span list — keeps us coherent if a
        // refresh raced the load.
        const seen = new Set(spans.map((s) => s.id));
        for (const s of trace.spans) {
          if (!seen.has(s.id)) {
            spans = [...spans, s];
            seen.add(s.id);
          }
        }
      },
      onSpan: (span) => {
        // Skip duplicates by span id.
        if (spans.some((s) => s.id === span.id)) return;
        spans = [...spans, span];
      },
      onComplete: (state) => {
        finalState = state;
        live = false;
      }
    });
  });

  onDestroy(() => {
    streamHandle?.close();
  });
</script>

<svelte:head>
  <title>Trace {data.trace.run_id} · selfevals</title>
</svelte:head>

<div class="grid grid-cols-[420px_1fr] min-h-screen">
  <aside class="border-r border-border bg-surface px-5 py-7 overflow-y-auto">
    <div class="text-xs text-text-3 font-mono mb-1">Trace</div>
    <h1 class="text-lg font-mono font-medium mb-4">{data.trace.run_id}</h1>
    <dl class="space-y-2 text-xs mb-6">
      <div class="flex justify-between items-center">
        <dt class="text-text-3">final state</dt>
        <dd class="font-mono flex items-center gap-2">
          {#if live}
            <span
              class="inline-block h-1.5 w-1.5 rounded-full bg-success animate-pulse"
              aria-label="live"
            ></span>
            <span class="text-success">live</span>
          {:else}
            {finalState}
          {/if}
        </dd>
      </div>
      {#if data.trace.iteration !== null}
        <div class="flex justify-between">
          <dt class="text-text-3">iteration</dt>
          <dd class="font-mono" data-numeric>#{data.trace.iteration}</dd>
        </div>
      {/if}
      <div class="flex justify-between">
        <dt class="text-text-3">spans</dt>
        <dd class="font-mono" data-numeric>{spans.length}</dd>
      </div>
    </dl>

    <div class="text-xs uppercase tracking-wide text-text-3 mb-2">Spans</div>
    <ul class="text-sm space-y-px">
      {#each tree.get(null) ?? [] as root}
        <SpanNode
          node={root}
          depth={0}
          {tree}
          {selected}
          setSelected={(s) => (selected = s)}
        />
      {/each}
    </ul>
  </aside>

  <main class="px-10 py-10 overflow-y-auto">
    {#if selected}
      <div class="text-xs text-text-3 mb-1 font-mono">
        {selected.kind} · {selected.duration_ms}ms
      </div>
      <h2 class="text-xl font-semibold mb-6">{selected.name}</h2>

      {#if selected.kind === 'llm_call'}
        <section class="grid grid-cols-3 gap-4 mb-6">
          {#each llmFacets(selected) as item}
            <div class="rounded-lg border border-border bg-surface px-4 py-3">
              <div class="text-xs uppercase tracking-wide text-text-3 mb-1">{item.label}</div>
              <div class="font-mono text-sm truncate">{item.value ?? '—'}</div>
            </div>
          {/each}
        </section>
      {/if}

      <pre class="font-mono text-xs bg-surface border border-border rounded-lg p-5 overflow-x-auto">{JSON.stringify(selected.detail, null, 2)}</pre>
    {:else}
      <div class="text-text-3 text-sm">Select a span to inspect.</div>
    {/if}
  </main>
</div>

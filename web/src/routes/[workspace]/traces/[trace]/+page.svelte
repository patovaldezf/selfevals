<script lang="ts">
  import TraceSidebar from '$lib/components/TraceSidebar.svelte';
  import SpanDetailPanel from '$lib/components/SpanDetailPanel.svelte';
  import PromotionModal from '$lib/components/PromotionModal.svelte';
  import type { SpanSummary } from '$lib/api/client';
  import { openTraceStream, type StreamHandle } from '$lib/api/sse';
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import type { PageData } from './$types';

  export let data: PageData;

  // `[workspace]` is a required route param, so it is always present here.
  $: workspaceId = $page.params.workspace as string;

  let selected: SpanSummary | null = null;
  let promoteOpen = false;

  // Live state: starts from the server-loaded snapshot and is augmented by
  // SSE events. We don't mutate `data.trace` directly so refetches stay
  // clean. Kept on the page (not a store) because both the sidebar tree and
  // the detail panel need it, and no other route shares this stream.
  let spans: SpanSummary[] = [...data.trace.spans];
  let finalState = data.trace.final_state;
  let live = false;
  let streamHandle: StreamHandle | null = null;

  $: tree = (() => {
    const byParent = new Map<string | null, SpanSummary[]>();
    for (const s of spans) {
      const list = byParent.get(s.parent_id) ?? [];
      list.push(s);
      byParent.set(s.parent_id, list);
    }
    for (const list of byParent.values()) {
      list.sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
    }
    return byParent;
  })();

  // The trace as a whole failed if the run ended in a non-completed state.
  $: traceFailed = ['error', 'failed', 'aborted', 'cancelled'].includes(finalState?.toLowerCase());

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

  $: traceTitle = data.trace.experiment_name ?? 'Standalone trace';
</script>

<svelte:head>
  <title>{traceTitle} · selfevals</title>
</svelte:head>

<div class="viewer">
  <TraceSidebar
    {workspaceId}
    trace={data.trace}
    {traceTitle}
    {live}
    {finalState}
    spanCount={spans.length}
    {tree}
    {selected}
    on:select={(e) => (selected = e.detail)}
    on:promote={() => (promoteOpen = true)}
  />

  <main class="detail">
    <SpanDetailPanel
      {selected}
      {traceFailed}
      {finalState}
      {workspaceId}
      traceId={data.trace.id}
      {live}
    />
  </main>
</div>

<PromotionModal
  {workspaceId}
  traceId={data.trace.id}
  open={promoteOpen}
  on:close={() => (promoteOpen = false)}
/>

<style>
  .viewer {
    display: grid;
    grid-template-columns: 440px 1fr;
    min-height: 100vh;
  }
  .detail {
    padding: 2.5rem;
    overflow-y: auto;
  }
</style>

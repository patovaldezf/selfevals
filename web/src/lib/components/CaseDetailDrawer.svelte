<!--
  CaseDetailDrawer — one case opened as its full execution, not a summary card.

  Clicking a case row in the Results tab opens this. It answers "what actually
  happened on this case" without leaving the list:
    - the aggregate verdict + expected-vs-detected diff (CaseResultRow)
    - structured_output the agent produced, when the case declared one
    - the raw per-grader verdicts (GraderResults — reasons, sub-scores)
    - the full conversation, every turn, when the case is multi-turn
    - the agent's span waterfall (SpanTreeFlat) with the selected span's detail
      (SpanDetailPanel) — tool call input/output, prompts, agent state — loaded
      on demand from the trace, reusing the exact renderers the trace viewer uses

  Wide (`xl`) so the waterfall + span detail sit side by side. Runes.
-->
<script lang="ts">
  import type { ScenarioResult, SpanSummary, TraceDetail } from '$lib/api/client';
  import { api, ApiError } from '$lib/api/client';
  import Drawer from './ui/Drawer.svelte';
  import Button from './ui/Button.svelte';
  import GradeChip from './GradeChip.svelte';
  import CaseResultRow from './CaseResultRow.svelte';
  import GraderResults from './GraderResults.svelte';
  import SpanTreeFlat from './SpanTreeFlat.svelte';
  import SpanDetailPanel from './SpanDetailPanel.svelte';

  let {
    open = false,
    result,
    workspaceId,
    onClose
  }: {
    open?: boolean;
    result: ScenarioResult | null;
    workspaceId: string;
    onClose: () => void;
  } = $props();

  const traceRef = $derived(result?.run_id ?? result?.trace_id ?? null);
  const title = $derived(result?.case_name ?? result?.case_id ?? 'Case');
  const structuredOutput = $derived(result?.detected?.structured_output ?? null);
  const isConversation = $derived((result?.turns?.length ?? 0) > 0);

  // Trace (span waterfall) loaded lazily when the drawer opens on a case that
  // has one. The span list can be large and lives behind its own endpoint, so
  // we never pull it until the reader asks for this case. A request token drops
  // a stale response if the reader clicks through cases quickly.
  type TraceState =
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'loaded'; trace: TraceDetail }
    | { kind: 'error'; message: string }
    | { kind: 'none' };
  let traceState = $state<TraceState>({ kind: 'idle' });
  let selectedSpan = $state<SpanSummary | null>(null);
  let traceToken = 0;

  async function loadTrace(ref: string): Promise<void> {
    const token = ++traceToken;
    traceState = { kind: 'loading' };
    selectedSpan = null;
    try {
      const trace = await api.trace(workspaceId, ref);
      if (token !== traceToken) return;
      traceState = { kind: 'loaded', trace };
      // Preselect the first failed span if any, else the root — so the reader
      // lands on the interesting thing, not an empty pane.
      const failed = trace.spans.find((s) => spanIsError(s));
      selectedSpan = failed ?? trace.spans[0] ?? null;
    } catch (err) {
      if (token !== traceToken) return;
      traceState =
        err instanceof ApiError && err.status === 404
          ? { kind: 'none' }
          : { kind: 'error', message: err instanceof ApiError ? err.detail : String(err) };
    }
  }

  function spanIsError(s: SpanSummary): boolean {
    if (s.kind === 'error') return true;
    const fs = s.detail.final_state as { status?: string } | undefined;
    const status = (s.detail.status as string | undefined) ?? fs?.status;
    return status === 'error' || status === 'failed' || s.detail.error != null;
  }

  // When the drawer opens (or the case changes), (re)load the trace. When it
  // closes, drop the trace so a reopen starts clean and we don't hold span
  // lists in memory across cases.
  $effect(() => {
    if (open && traceRef) {
      void loadTrace(traceRef);
    } else if (!open) {
      traceState = { kind: 'idle' };
      selectedSpan = null;
    }
  });

  const trace = $derived(traceState.kind === 'loaded' ? traceState.trace : null);

  // Parent → children map, sorted by start time — the same shape SpanTreeFlat
  // and the trace page build.
  const tree = $derived.by(() => {
    const byParent = new Map<string | null, SpanSummary[]>();
    if (!trace) return byParent;
    for (const s of trace.spans) {
      const list = byParent.get(s.parent_id) ?? [];
      list.push(s);
      byParent.set(s.parent_id, list);
    }
    for (const list of byParent.values()) {
      list.sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
    }
    return byParent;
  });

  const traceFailed = $derived(
    trace
      ? ['error', 'failed', 'aborted', 'cancelled'].includes(trace.final_state?.toLowerCase())
      : false
  );
</script>

<Drawer {open} size="xl" title={undefined} on:close={onClose}>
  {#if result}
    <div class="case">
      <header class="case-head">
        <div class="ident">
          <h2 class="name">{title}</h2>
          <GradeChip grade={result.label ?? null} />
        </div>
        {#if result.case_name && result.case_id !== result.case_name}
          <span class="case-id font-mono">{result.case_id}</span>
        {/if}
      </header>

      {#if result.message}
        <p class="message">{result.message}</p>
      {/if}

      <!-- Expected-vs-detected diff, reusing the one renderer. asTurn keeps the
           row's own header/turns out; we render the thread + trace ourselves. -->
      <section class="block">
        <h3 class="block-title">Expected vs detected</h3>
        <CaseResultRow {result} {workspaceId} asTurn showTurns={false} />
      </section>

      {#if structuredOutput}
        <section class="block">
          <h3 class="block-title">Structured output</h3>
          <pre class="raw">{JSON.stringify(structuredOutput, null, 2)}</pre>
        </section>
      {/if}

      {#if result.grader_results.length}
        <section class="block">
          <h3 class="block-title">Graders</h3>
          <GraderResults results={result.grader_results} />
        </section>
      {/if}

      {#if isConversation}
        <section class="block">
          <h3 class="block-title">Conversation · {result.turns.length} turns</h3>
          <div class="turns">
            {#each result.turns as turn (turn.case_id + (turn.position ?? 0))}
              <CaseResultRow result={turn} {workspaceId} asTurn showTurns={false} />
            {/each}
          </div>
        </section>
      {/if}

      {#if traceRef}
        <section class="block">
          <h3 class="block-title">Execution trace</h3>
          {#if traceState.kind === 'loading'}
            <div class="trace-status">Loading span waterfall…</div>
          {:else if traceState.kind === 'none'}
            <div class="trace-status">
              No trace persisted for this case. Passing cases carry no trace unless the run used
              <code class="font-mono">persist_traces: all</code>.
            </div>
          {:else if traceState.kind === 'error'}
            <div class="trace-status trace-error">Could not load trace: {traceState.message}</div>
          {:else if trace}
            <div class="waterfall">
              <div class="tree-col">
                <SpanTreeFlat
                  {tree}
                  selected={selectedSpan}
                  setSelected={(s) => (selectedSpan = s)}
                />
              </div>
              <div class="detail-col">
                <SpanDetailPanel
                  selected={selectedSpan}
                  {traceFailed}
                  finalState={trace.final_state}
                />
              </div>
            </div>
          {/if}
        </section>
      {/if}
    </div>
  {/if}

  <svelte:fragment slot="footer">
    {#if traceRef}
      <Button variant="secondary" size="sm" href={`/${workspaceId}/traces/${traceRef}`}>
        Open full trace
      </Button>
    {/if}
    <Button variant="ghost" size="sm" on:click={onClose}>Close</Button>
  </svelte:fragment>
</Drawer>

<style>
  .case {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }
  .case-head {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .ident {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    min-width: 0;
  }
  .name {
    font-size: var(--text-md);
    font-weight: 600;
    color: var(--color-text-1);
    overflow-wrap: anywhere;
  }
  .case-id {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .message {
    font-size: var(--text-sm);
    line-height: var(--leading-snug);
    color: var(--color-text-2);
    overflow-wrap: anywhere;
  }
  .block {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .block-title {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .turns {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .raw {
    padding: 0.75rem;
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    line-height: 1.5;
    color: var(--color-text-1);
    overflow-x: auto;
    max-height: 20rem;
  }
  .trace-status {
    font-size: var(--text-sm);
    color: var(--color-text-3);
    padding: 0.75rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }
  .trace-error {
    color: var(--color-bad);
  }
  /* Waterfall + span detail side by side. The tree scrolls within its column
     (it windows internally); the detail column scrolls independently. */
  .waterfall {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
    gap: 1rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    overflow: hidden;
  }
  .tree-col {
    padding: 0.5rem;
    border-right: 1px solid var(--color-border);
    background: var(--color-surface);
    min-width: 0;
  }
  .detail-col {
    padding: 0.85rem;
    background: var(--color-surface);
    max-height: 60vh;
    overflow-y: auto;
    min-width: 0;
  }
  @media (max-width: 60rem) {
    .waterfall {
      grid-template-columns: 1fr;
    }
    .tree-col {
      border-right: none;
      border-bottom: 1px solid var(--color-border);
    }
  }
</style>

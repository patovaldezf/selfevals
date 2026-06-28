<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import PointerField from '$lib/components/PointerField.svelte';
  import SpanTreeFlat from '$lib/components/SpanTreeFlat.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { api, ApiError } from '$lib/api/client';
  import { factsFor } from '$lib/spans/facts';
  import { styleForKind } from '$lib/spans/kindStyle';
  import type {
    AppendDatasetCaseResult,
    DatasetDetail,
    DatasetSummary,
    SpanSummary
  } from '$lib/api/client';
  import { openTraceStream, type StreamHandle } from '$lib/api/sse';
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import type { PageData } from './$types';
  import { ArrowRight, AlertCircle, FlaskConical, X } from 'lucide-svelte';

  export let data: PageData;

  let selected: SpanSummary | null = null;
  let promoteOpen = false;
  let promoteLoading = false;
  let promoteSaving = false;
  let promoteError: string | null = null;
  let promoteDraftText = '';
  let regressionDatasets: DatasetSummary[] = [];
  let selectedDatasetId = '';
  let newDatasetName = 'agent regressions';
  let promoteResult:
    | AppendDatasetCaseResult
    | { dataset: DatasetDetail; case_id: string; created_new_dataset: boolean }
    | null = null;

  // Pointer fields by span kind. The schema (schemas/trace.py) puts these
  // on every span as `<name>_pointer` + `<name>_hash`. We surface them as
  // PointerField widgets so the user can click to resolve the actual
  // bytes — without this, every `*_pointer: "oss://..."` in the JSON
  // dump is opaque and the trace viewer is debug theater.
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

  $: pointerFields =
    selected && POINTERS_BY_KIND[selected.kind]
      ? POINTERS_BY_KIND[selected.kind].map((p) => ({
          ...p,
          ...readPointer(selected!.detail, p.field)
        }))
      : [];
  $: hasAnyPointer = pointerFields.some((p) => p.pointer !== null);

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

  // Is a span a failure? Mirrors the tree's logic so the detail pane can flag it.
  function spanError(s: SpanSummary): string | null {
    if (typeof s.detail.error === 'string') return s.detail.error;
    const fs = s.detail.final_state as { status?: string; error?: string } | undefined;
    if (fs?.error) return fs.error;
    if (s.kind === 'error') return (s.detail.message as string | undefined) ?? 'error';
    return null;
  }

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

  async function openPromote() {
    const workspaceId = $page.params.workspace;
    if (!workspaceId) return;
    promoteOpen = true;
    promoteLoading = true;
    promoteSaving = false;
    promoteError = null;
    promoteResult = null;
    try {
      const [draft, datasets] = await Promise.all([
        api.draftCaseFromTrace(workspaceId, data.trace.id),
        api.listDatasets(workspaceId, undefined, { dataset_type: 'regression', limit: 100 })
      ]);
      promoteDraftText = JSON.stringify(draft.case, null, 2);
      regressionDatasets = datasets.items;
      selectedDatasetId = datasets.items[0]?.id ?? '__new';
    } catch (e) {
      promoteError = e instanceof ApiError ? e.detail : 'Could not draft regression case.';
    } finally {
      promoteLoading = false;
    }
  }

  async function savePromotion() {
    const workspaceId = $page.params.workspace;
    if (!workspaceId) return;
    promoteSaving = true;
    promoteError = null;
    try {
      const parsed = JSON.parse(promoteDraftText) as Record<string, unknown>;
      if (selectedDatasetId === '__new') {
        const dataset = await api.createDataset(workspaceId, {
          name: newDatasetName.trim() || 'agent regressions',
          dataset_type: 'regression',
          cases: [parsed]
        });
        promoteResult = {
          dataset,
          case_id: typeof parsed.id === 'string' ? parsed.id : 'unknown',
          created_new_dataset: true
        };
      } else {
        promoteResult = await api.appendDatasetCase(workspaceId, selectedDatasetId, {
          case: parsed,
          create_version_if_frozen: true
        });
      }
    } catch (e) {
      if (e instanceof SyntaxError) {
        promoteError = 'Case JSON is invalid.';
      } else {
        promoteError = e instanceof ApiError ? e.detail : 'Could not save regression case.';
      }
    } finally {
      promoteSaving = false;
    }
  }

  $: runCommand =
    promoteResult &&
    `selfevals --db ./selfevals.sqlite run <spec.yaml> --dataset ${promoteResult.dataset.id}`;
  $: gateCommand =
    promoteResult &&
    `selfevals --db ./selfevals.sqlite regression check ${$page.params.workspace} --dataset ${promoteResult.dataset.id} --iteration <new_iteration_id>`;
</script>

<svelte:head>
  <title>{traceTitle} · selfevals</title>
</svelte:head>

<div class="viewer">
  <aside class="sidebar">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href={`/${$page.params.workspace}`}>workspace</a>
      <span aria-hidden="true">/</span>
      <span>trace</span>
    </nav>

    {#if data.trace.experiment_name && data.trace.experiment_id}
      <h1 class="title">
        <a href={`/${$page.params.workspace}/experiments/${data.trace.experiment_id}`}>
          {data.trace.experiment_name}
        </a>
      </h1>
      {#if data.trace.iteration !== null}
        <div class="subtitle">Iteration #{data.trace.iteration}</div>
      {/if}
    {:else}
      <h1 class="title">{traceTitle}</h1>
    {/if}

    <div class="state-row">
      {#if live}
        <span class="state-pill">
          <StatusDot state="running" />
          <span class="state-label">live</span>
        </span>
      {:else}
        <span class="state-pill">
          <StatusDot state={finalState} />
          <span class="state-label">{finalState}</span>
        </span>
      {/if}
      <span class="spans-count" data-numeric>{spans.length} spans</span>
    </div>

    <dl class="meta">
      {#if data.trace.thread_id}
        <div class="meta-row meta-row-col">
          <dt>thread</dt>
          <dd>
            <a
              class="thread-link"
              href={`/${$page.params.workspace}/threads/${data.trace.thread_id}`}
            >
              <span>View conversation</span>
              <Icon icon={ArrowRight} size={13} />
            </a>
            <CopyableId id={data.trace.thread_id} label="thread id" />
            {#if data.trace.thread_position !== null}
              <span class="turn" data-numeric>turn {data.trace.thread_position}</span>
            {/if}
          </dd>
        </div>
      {/if}
      <div class="meta-row">
        <dt>run id</dt>
        <dd><CopyableId id={data.trace.run_id} label="run id" /></dd>
      </div>
      {#if data.trace.experiment_id}
        <div class="meta-row">
          <dt>experiment id</dt>
          <dd><CopyableId id={data.trace.experiment_id} label="experiment id" /></dd>
        </div>
      {/if}
    </dl>

    <div class="promote-cta">
      <Button variant="brand" size="sm" on:click={openPromote}>
        <Icon icon={FlaskConical} size={14} />
        Promote to regression case
      </Button>
      <p class="promote-hint">Turn this run into permanent test coverage.</p>
    </div>

    <div class="tree-head">
      <span>Spans</span>
      <span class="tree-hint">↑↓ / j k to move</span>
    </div>
    <SpanTreeFlat {tree} {selected} setSelected={(s) => (selected = s)} />
  </aside>

  <main class="detail">
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
  </main>
</div>

{#if promoteOpen}
  <div class="scrim" on:click={() => (promoteOpen = false)} aria-hidden="true"></div>
  <div class="promote-positioner">
    <div class="promote" role="dialog" aria-modal="true" aria-label="Promote to regression case">
      <div class="promote-bar">
        <div>
          <div class="promote-eyebrow">Regression case</div>
          <h2 class="promote-title">Promote this trace</h2>
        </div>
        <button type="button" class="promote-close" on:click={() => (promoteOpen = false)}>
          <Icon icon={X} size={16} />
        </button>
      </div>

      <div class="promote-body">
        <div class="promote-editor">
          {#if promoteLoading}
            <div class="promote-loading">Building case draft…</div>
          {:else}
            <label class="promote-field">
              <span class="promote-field-label">Editable EvalCase JSON</span>
              <textarea bind:value={promoteDraftText} spellcheck="false"></textarea>
            </label>
          {/if}
        </div>

        <aside class="promote-side">
          <div class="promote-field">
            <label class="promote-field-label" for="dataset-target">Target dataset</label>
            <select
              id="dataset-target"
              bind:value={selectedDatasetId}
              disabled={promoteLoading || promoteSaving}
            >
              <option value="__new">Create new regression dataset</option>
              {#each regressionDatasets as ds}
                <option value={ds.id}>{ds.name} · {ds.status} · {ds.case_count} cases</option>
              {/each}
            </select>
          </div>

          {#if selectedDatasetId === '__new'}
            <label class="promote-field">
              <span class="promote-field-label">New dataset name</span>
              <input bind:value={newDatasetName} disabled={promoteSaving} />
            </label>
          {/if}

          <div class="promote-note">
            The draft keeps the original expected answer. Review it before saving; this becomes
            regression coverage.
          </div>

          {#if promoteError}
            <div class="promote-err">{promoteError}</div>
          {/if}

          <Button
            variant="brand"
            on:click={savePromotion}
            disabled={promoteLoading || promoteSaving}
            loading={promoteSaving}
          >
            {promoteSaving ? 'Saving…' : 'Save regression case'}
          </Button>

          {#if promoteResult}
            <div class="promote-saved">
              <div class="promote-saved-title">Saved</div>
              <div class="promote-saved-row">
                <span>dataset</span>
                <CopyableId id={promoteResult.dataset.id} label="dataset id" />
              </div>
              <div class="promote-saved-row">
                <span>case</span>
                <CopyableId id={promoteResult.case_id} label="case id" />
              </div>
              {#if promoteResult.created_new_dataset}
                <div class="promote-saved-note">Created a new active dataset.</div>
              {/if}
              {#if runCommand}
                <div class="promote-cmd">
                  <div class="promote-cmd-label">Run</div>
                  <pre>{runCommand}</pre>
                </div>
              {/if}
              {#if gateCommand}
                <div class="promote-cmd">
                  <div class="promote-cmd-label">Gate</div>
                  <pre>{gateCommand}</pre>
                </div>
              {/if}
            </div>
          {/if}
        </aside>
      </div>
    </div>
  </div>
{/if}

<style>
  .viewer {
    display: grid;
    grid-template-columns: 440px 1fr;
    min-height: 100vh;
  }
  .sidebar {
    border-right: 1px solid var(--color-border);
    background: var(--color-surface);
    padding: 1.5rem 1.25rem;
    overflow-y: auto;
  }
  .crumbs {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 0.75rem;
  }
  .crumbs a:hover {
    color: var(--color-text-1);
  }
  .title {
    font-size: var(--text-lg);
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: var(--leading-snug);
  }
  .title a:hover {
    color: var(--color-text-2);
  }
  .subtitle {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-top: 0.2rem;
  }
  .state-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0.9rem 0 1.1rem;
  }
  .state-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.25rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg);
  }
  .state-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
    text-transform: capitalize;
  }
  .spans-count {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .meta {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
    margin-bottom: 1.25rem;
  }
  .meta-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .meta-row-col {
    align-items: flex-start;
  }
  .meta-row dt {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .meta-row-col dd {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.35rem;
    min-width: 0;
  }
  .thread-link {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    color: var(--color-text-2);
    font-size: var(--text-xs);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .thread-link:hover {
    color: var(--color-text-1);
  }
  .turn {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .promote-cta {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.9rem 0;
    margin-bottom: 0.5rem;
    border-top: 1px solid var(--color-border);
    border-bottom: 1px solid var(--color-border);
  }
  .promote-hint {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .tree-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin: 1rem 0 0.5rem;
  }
  .tree-head > span:first-child {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .tree-hint {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .detail {
    padding: 2.5rem;
    overflow-y: auto;
  }
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

  /* Promote modal */
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(10, 10, 10, 0.4);
    z-index: 50;
  }
  .promote-positioner {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem;
    pointer-events: none;
  }
  .promote {
    pointer-events: auto;
    width: 100%;
    max-width: 64rem;
    max-height: 90vh;
    overflow: hidden;
    background: var(--color-bg);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-3);
    display: flex;
    flex-direction: column;
  }
  .promote-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    border-bottom: 1px solid var(--color-border);
    padding: 0.9rem 1.25rem;
  }
  .promote-eyebrow {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .promote-title {
    font-size: var(--text-md);
    font-weight: 600;
  }
  .promote-close {
    color: var(--color-text-3);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .promote-close:hover {
    color: var(--color-text-1);
  }
  .promote-body {
    display: grid;
    grid-template-columns: 1fr 340px;
    min-height: 0;
    flex: 1;
  }
  .promote-editor {
    padding: 1.25rem;
    overflow-y: auto;
  }
  .promote-loading {
    color: var(--color-text-3);
    font-size: var(--text-sm);
    text-align: center;
    padding: 5rem 0;
  }
  .promote-field {
    display: block;
  }
  .promote-field-label {
    display: block;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.5rem;
  }
  .promote-editor textarea {
    width: 100%;
    height: 56vh;
    resize: none;
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-md);
    padding: 1rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-1);
    outline: none;
  }
  .promote-editor textarea:focus {
    box-shadow: 0 0 0 2px var(--color-brand-subtle);
    border-color: var(--color-brand);
  }
  .promote-side {
    border-left: 1px solid var(--color-border);
    background: var(--color-surface);
    padding: 1.25rem;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 1.1rem;
  }
  .promote-side select,
  .promote-side input {
    width: 100%;
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    border-radius: var(--radius-md);
    padding: 0.5rem;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .promote-note {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    border-radius: var(--radius-md);
    padding: 0.7rem 0.8rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
    line-height: var(--leading-snug);
  }
  .promote-err {
    border: 1px solid var(--color-bad);
    color: var(--color-bad);
    border-radius: var(--radius-md);
    padding: 0.7rem 0.8rem;
    font-size: var(--text-xs);
  }
  .promote-saved {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    border-top: 1px solid var(--color-border);
    padding-top: 1rem;
  }
  .promote-saved-title {
    font-size: var(--text-sm);
    font-weight: 600;
  }
  .promote-saved-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .promote-saved-note {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .promote-cmd-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.25rem;
  }
  .promote-cmd pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    padding: 0.5rem;
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
  }
</style>

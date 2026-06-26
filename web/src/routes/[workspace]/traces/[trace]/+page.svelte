<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import PointerField from '$lib/components/PointerField.svelte';
  import SpanTreeFlat from '$lib/components/SpanTreeFlat.svelte';
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

<div class="grid grid-cols-[420px_1fr] min-h-screen">
  <aside class="border-r border-border bg-surface px-5 py-7 overflow-y-auto">
    <div class="text-xs text-text-3 uppercase tracking-wide mb-1">Trace</div>
    {#if data.trace.experiment_name && data.trace.experiment_id}
      <h1 class="text-lg font-semibold tracking-tight mb-1">
        <a
          href={`/${$page.params.workspace}/experiments/${data.trace.experiment_id}`}
          class="hover:text-text-1"
        >
          {data.trace.experiment_name}
        </a>
      </h1>
      {#if data.trace.iteration !== null}
        <div class="text-text-3 text-xs mb-4">Iteration #{data.trace.iteration}</div>
      {:else}
        <div class="mb-4"></div>
      {/if}
    {:else}
      <h1 class="text-lg font-semibold tracking-tight mb-4">{traceTitle}</h1>
    {/if}
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
      {#if data.trace.thread_id}
        <div class="flex justify-between items-start gap-2">
          <dt class="text-text-3 pt-0.5">thread</dt>
          <dd class="flex flex-col items-end gap-1 min-w-0">
            <a
              href={`/${$page.params.workspace}/threads/${data.trace.thread_id}`}
              class="group inline-flex items-center gap-1.5 text-text-2 hover:text-text-1 transition-colors"
            >
              <span>View conversation</span>
              <span class="text-text-3 group-hover:text-text-1 shrink-0" aria-hidden="true">→</span>
            </a>
            <CopyableId id={data.trace.thread_id} label="thread id" />
            {#if data.trace.thread_position !== null}
              <span class="font-mono text-text-3" data-numeric
                >turn {data.trace.thread_position}</span
              >
            {/if}
          </dd>
        </div>
      {/if}
      <div class="flex justify-between">
        <dt class="text-text-3">spans</dt>
        <dd class="font-mono" data-numeric>{spans.length}</dd>
      </div>
      <div class="flex justify-between items-center gap-2">
        <dt class="text-text-3">run id</dt>
        <dd class="min-w-0"><CopyableId id={data.trace.run_id} label="run id" /></dd>
      </div>
      {#if data.trace.experiment_id}
        <div class="flex justify-between items-center gap-2">
          <dt class="text-text-3">experiment id</dt>
          <dd class="min-w-0">
            <CopyableId id={data.trace.experiment_id} label="experiment id" />
          </dd>
        </div>
      {/if}
    </dl>

    <button
      type="button"
      class="w-full rounded-md border border-border bg-surface-2 px-3 py-2.5 text-sm text-text-1 hover:bg-bg transition-colors mb-6"
      on:click={openPromote}
    >
      Promote to regression case
    </button>

    <div
      class="text-xs uppercase tracking-wide text-text-3 mb-2 flex items-baseline justify-between"
    >
      <span>Spans</span>
      <span class="text-text-3 font-mono normal-case" data-numeric>{spans.length}</span>
    </div>
    <SpanTreeFlat {tree} {selected} setSelected={(s) => (selected = s)} />
  </aside>

  <main class="px-10 py-10 overflow-y-auto">
    {#if selected}
      {@const selectedStyle = styleForKind(selected.kind)}
      {@const selectedFacts = factsFor(selected)}
      <div class="flex items-center gap-2 text-xs text-text-3 mb-1">
        <span
          class="inline-block text-[14px] leading-none"
          style:color={selectedStyle.color}
          aria-hidden="true">{selectedStyle.glyph}</span
        >
        <span class="font-mono uppercase tracking-wide">{selectedStyle.label}</span>
        <span aria-hidden="true">·</span>
        <span class="font-mono" data-numeric>{selected.duration_ms}ms</span>
        {#each selectedFacts as f (f.key)}
          <span aria-hidden="true">·</span>
          <span class="font-mono" data-numeric title={f.title ?? f.key}>{f.value}</span>
        {/each}
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

      {#if pointerFields.length > 0}
        <section class="mb-6 rounded-lg border border-border bg-surface px-5 py-4">
          <div
            class="text-xs uppercase tracking-wide text-text-3 mb-3 flex items-baseline justify-between"
          >
            <span>Payloads</span>
            {#if !hasAnyPointer}
              <span class="text-text-3 normal-case italic">none captured for this span</span>
            {/if}
          </div>
          {#if hasAnyPointer}
            <div class="space-y-4">
              {#each pointerFields as f (f.field)}
                {#if f.pointer}
                  <PointerField label={f.label} pointer={f.pointer} hash={f.hash} />
                {/if}
              {/each}
            </div>
          {/if}
        </section>
      {/if}

      <details class="group">
        <summary
          class="text-xs uppercase tracking-wide text-text-3 cursor-pointer hover:text-text-1 mb-2 list-none flex items-center gap-1.5"
        >
          <span class="group-open:rotate-90 transition-transform" aria-hidden="true">›</span>
          Raw detail
        </summary>
        <pre
          class="font-mono text-xs bg-surface border border-border rounded-lg p-5 overflow-x-auto">{JSON.stringify(
            selected.detail,
            null,
            2
          )}</pre>
      </details>
    {:else}
      <div class="text-text-3 text-sm">Select a span to inspect.</div>
    {/if}
  </main>
</div>

{#if promoteOpen}
  <div class="fixed inset-0 z-50 bg-black/40 flex items-center justify-center px-6 py-8">
    <section
      class="w-full max-w-5xl max-h-[90vh] overflow-hidden rounded-lg border border-border bg-bg shadow-xl"
    >
      <div class="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <div class="text-xs uppercase tracking-wide text-text-3">Regression case</div>
          <h2 class="text-lg font-semibold">Promote this trace</h2>
        </div>
        <button
          type="button"
          class="text-text-3 hover:text-text-1 text-sm"
          on:click={() => (promoteOpen = false)}
        >
          Close
        </button>
      </div>

      <div class="grid grid-cols-[1fr_320px] min-h-0 max-h-[calc(90vh-73px)]">
        <div class="p-5 overflow-y-auto">
          {#if promoteLoading}
            <div class="text-text-3 text-sm py-20 text-center">Building case draft...</div>
          {:else}
            <label class="block">
              <span class="text-xs uppercase tracking-wide text-text-3">Editable EvalCase JSON</span
              >
              <textarea
                class="mt-2 h-[58vh] w-full resize-none rounded border border-border bg-surface p-4 font-mono text-xs text-text-1 outline-none focus:ring-2 focus:ring-text-1"
                bind:value={promoteDraftText}
                spellcheck="false"
              ></textarea>
            </label>
          {/if}
        </div>

        <aside class="border-l border-border bg-surface px-5 py-5 overflow-y-auto">
          <div class="space-y-5">
            <div>
              <label class="text-xs uppercase tracking-wide text-text-3" for="dataset-target">
                Target dataset
              </label>
              <select
                id="dataset-target"
                class="mt-2 w-full rounded border border-border bg-bg px-2 py-2 text-sm"
                bind:value={selectedDatasetId}
                disabled={promoteLoading || promoteSaving}
              >
                <option value="__new">Create new regression dataset</option>
                {#each regressionDatasets as ds}
                  <option value={ds.id}>
                    {ds.name} · {ds.status} · {ds.case_count} cases
                  </option>
                {/each}
              </select>
            </div>

            {#if selectedDatasetId === '__new'}
              <label class="block">
                <span class="text-xs uppercase tracking-wide text-text-3">New dataset name</span>
                <input
                  class="mt-2 w-full rounded border border-border bg-bg px-3 py-2 text-sm"
                  bind:value={newDatasetName}
                  disabled={promoteSaving}
                />
              </label>
            {/if}

            <div class="rounded border border-border bg-bg px-3 py-3 text-xs text-text-2">
              The draft keeps the original expected answer. Review it before saving; this becomes
              regression coverage.
            </div>

            {#if promoteError}
              <div
                class="rounded border px-3 py-3 text-xs"
                style:border-color="var(--color-danger)"
                style:color="var(--color-danger)"
              >
                {promoteError}
              </div>
            {/if}

            <button
              type="button"
              class="w-full rounded bg-text-1 px-3 py-2.5 text-sm font-medium text-bg disabled:opacity-50"
              disabled={promoteLoading || promoteSaving}
              on:click={savePromotion}
            >
              {promoteSaving ? 'Saving...' : 'Save regression case'}
            </button>

            {#if promoteResult}
              <div class="space-y-3 border-t border-border pt-4">
                <div class="text-sm font-medium">Saved</div>
                <div class="space-y-1 text-xs">
                  <div class="flex justify-between gap-2">
                    <span class="text-text-3">dataset</span>
                    <CopyableId id={promoteResult.dataset.id} label="dataset id" />
                  </div>
                  <div class="flex justify-between gap-2">
                    <span class="text-text-3">case</span>
                    <CopyableId id={promoteResult.case_id} label="case id" />
                  </div>
                  {#if promoteResult.created_new_dataset}
                    <div class="text-text-3">Created a new active dataset.</div>
                  {/if}
                </div>
                {#if runCommand}
                  <div>
                    <div class="text-xs uppercase tracking-wide text-text-3 mb-1">Run</div>
                    <pre
                      class="whitespace-pre-wrap break-words rounded bg-bg border border-border p-2 font-mono text-[11px]">{runCommand}</pre>
                  </div>
                {/if}
                {#if gateCommand}
                  <div>
                    <div class="text-xs uppercase tracking-wide text-text-3 mb-1">Gate</div>
                    <pre
                      class="whitespace-pre-wrap break-words rounded bg-bg border border-border p-2 font-mono text-[11px]">{gateCommand}</pre>
                  </div>
                {/if}
              </div>
            {/if}
          </div>
        </aside>
      </div>
    </section>
  </div>
{/if}

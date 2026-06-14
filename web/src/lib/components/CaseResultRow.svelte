<script lang="ts">
  /**
   * One `ScenarioResult` rendered as an expected-vs-detected diff — the core
   * "what did we ask for, what did we get, did it match" view. Reused by the
   * experiment Results tab (per case) and recursively for conversation turns.
   *
   * Design intent: a reader should see at a glance whether the case passed and,
   * if not, *which* declared dimension broke. Expected and detected sit side by
   * side; the specific substrings/tools that broke a rule are highlighted with
   * the same semantic tokens the rest of the app uses (success/danger), kept
   * sober — soft tints, not loud fills.
   */
  import type { ScenarioResult } from '$lib/api/client';
  import GradeChip from './GradeChip.svelte';

  export let result: ScenarioResult;
  export let workspaceId: string;
  /** Turn rows render denser and without the case-identity header. */
  export let asTurn = false;

  let turnsOpen = false;

  $: traceRef = result.run_id ?? result.trace_id ?? null;
  $: hasTrace = traceRef != null;
  $: hasExpected = result.expected && Object.values(result.expected).some((v) => v != null);
  $: hasDetected = result.detected && Object.values(result.detected).some((v) => v != null);
  // No persisted trace → detected/matched are null by contract, not an error.
  $: noTrace = result.matched == null && !hasDetected;

  function fmtVal(v: unknown): string {
    if (v == null) return '';
    if (typeof v === 'string') return v;
    return JSON.stringify(v);
  }

  type Field = { label: string; expected?: string[]; detected?: string[]; broken?: string[] };

  // Project the declared dimensions into uniform rows so expected and detected
  // align cell-for-cell. Only dimensions the case actually declared appear.
  $: fields = ((): Field[] => {
    const e = result.expected ?? {};
    const d = result.detected ?? {};
    const out: Field[] = [];
    if (e.structured_output != null || d.structured_output != null) {
      out.push({
        label: 'structured_output',
        expected: e.structured_output != null ? [fmtVal(e.structured_output)] : [],
        detected: d.structured_output != null ? [fmtVal(d.structured_output)] : []
      });
    }
    if (e.must_include?.length || d.missing?.length) {
      out.push({
        label: 'must_include',
        expected: e.must_include ?? [],
        detected: d.content != null ? [] : undefined,
        broken: d.missing ?? []
      });
    }
    if (e.must_not_include?.length || d.forbidden_present?.length) {
      out.push({
        label: 'must_not_include',
        expected: e.must_not_include ?? [],
        broken: d.forbidden_present ?? []
      });
    }
    if (e.required_tools?.length || d.tools_invoked?.length) {
      out.push({
        label: 'tools',
        expected: e.required_tools ?? [],
        detected: d.tools_invoked ?? []
      });
    }
    return out;
  })();
</script>

<div
  class="result {asTurn ? 'result-turn' : ''}"
  class:result-pass={result.matched === true}
  class:result-fail={result.matched === false}
>
  <div class="head">
    <div class="min-w-0 flex items-center gap-2">
      {#if asTurn}
        <span class="font-mono text-[11px] text-text-3">#{(result.position ?? 0) + 1}</span>
      {:else}
        <span class="font-medium text-sm truncate">{result.case_name ?? result.case_id}</span>
      {/if}
      <GradeChip grade={result.label ?? null} />
    </div>
    {#if hasTrace && traceRef}
      <a
        class="shrink-0 text-xs text-text-2 underline-offset-2 hover:text-text-1 hover:underline"
        href={`/${workspaceId}/traces/${traceRef}`}>trace →</a
      >
    {/if}
  </div>

  {#if result.message}
    <p class="message">{result.message}</p>
  {/if}

  {#if noTrace}
    <p class="text-xs text-text-3 italic mt-1.5">
      No trace persisted — passing cases carry no trace unless <code class="font-mono"
        >persist_traces: all</code
      >.
    </p>
  {:else if fields.length}
    <div class="diff">
      <div class="diff-head">
        <span>dimension</span><span>expected</span><span>detected</span>
      </div>
      {#each fields as f}
        <div class="diff-row">
          <span class="dim font-mono">{f.label}</span>
          <span class="cell">
            {#each f.expected ?? [] as v}<code class="tok">{v}</code>{/each}
            {#if !(f.expected ?? []).length}<span class="text-text-3">—</span>{/if}
          </span>
          <span class="cell">
            {#if f.broken?.length}
              {#each f.broken as v}<code class="tok tok-bad" title="broke this rule">{v}</code
                >{/each}
            {/if}
            {#each f.detected ?? [] as v}<code class="tok tok-ok">{v}</code>{/each}
            {#if !(f.detected ?? []).length && !(f.broken ?? []).length}
              <span class="text-text-3">—</span>
            {/if}
          </span>
        </div>
      {/each}
    </div>
  {/if}

  {#if result.failure_modes.length}
    <div class="modes">
      {#each result.failure_modes as m}
        <span class="mode-chip font-mono">{m}</span>
      {/each}
    </div>
  {/if}

  {#if result.turns.length}
    <button class="turns-toggle" on:click={() => (turnsOpen = !turnsOpen)}>
      {turnsOpen ? '▾' : '▸'}
      {result.turns.length} turn{result.turns.length === 1 ? '' : 's'}
    </button>
    {#if turnsOpen}
      <div class="turns">
        {#each result.turns as turn}
          <svelte:self result={turn} {workspaceId} asTurn />
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .result {
    border: 1px solid var(--color-border);
    border-left-width: 2px;
    border-radius: var(--radius-md);
    background: var(--color-surface);
    padding: 0.75rem 0.9rem;
  }
  .result-turn {
    padding: 0.5rem 0.7rem;
    background: var(--color-surface-2);
  }
  .result-pass {
    border-left-color: color-mix(in srgb, var(--color-success) 55%, var(--color-border));
  }
  .result-fail {
    border-left-color: color-mix(in srgb, var(--color-danger) 55%, var(--color-border));
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .message {
    margin-top: 0.4rem;
    font-size: 0.8rem;
    line-height: 1.4;
    color: var(--color-text-2);
    overflow-wrap: anywhere;
  }
  .diff {
    margin-top: 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    overflow: hidden;
    font-size: 0.75rem;
  }
  .diff-head,
  .diff-row {
    display: grid;
    grid-template-columns: 9rem 1fr 1fr;
    gap: 0.5rem;
  }
  .diff-head {
    background: var(--color-surface-2);
    color: var(--color-text-3);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 0.625rem;
    padding: 0.3rem 0.6rem;
  }
  .diff-row {
    padding: 0.4rem 0.6rem;
    border-top: 1px solid var(--color-border);
    align-items: start;
  }
  .dim {
    color: var(--color-text-2);
  }
  .cell {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    min-width: 0;
  }
  .tok {
    display: inline-block;
    padding: 0.05rem 0.35rem;
    border-radius: var(--radius-sm);
    background: var(--color-surface-2);
    color: var(--color-text-2);
    overflow-wrap: anywhere;
  }
  .tok-ok {
    color: var(--color-success);
    background: color-mix(in srgb, var(--color-success) 12%, transparent);
  }
  .tok-bad {
    color: var(--color-danger);
    background: color-mix(in srgb, var(--color-danger) 12%, transparent);
  }
  .modes {
    margin-top: 0.55rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .mode-chip {
    font-size: 10px;
    padding: 0.05rem 0.4rem;
    border-radius: var(--radius-sm);
    color: var(--color-danger);
    background: color-mix(in srgb, var(--color-danger) 10%, transparent);
  }
  .turns-toggle {
    margin-top: 0.55rem;
    font-size: 0.7rem;
    color: var(--color-text-3);
    transition: color 0.12s ease;
  }
  .turns-toggle:hover {
    color: var(--color-text-1);
  }
  .turns {
    margin-top: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
</style>

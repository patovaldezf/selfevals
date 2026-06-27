<!--
  One turn in a thread conversation.

  Layout: a left rail carries the at-a-glance signals (position pill +
  GradeChip), the body carries context (timestamp + classified message), a
  quiet escalation ("Open trace →" to zoom into this turn's full span tree),
  and a collapsible for the raw grader results. Designed to read
  top-to-bottom chronologically with calm, generous spacing.

  `turn` is a `ScenarioResult` (the same shape `/results` uses) — so a turn
  and a case render identically. `started_at`/`position` may be null for
  scenarios without a persisted trace; we guard accordingly.
-->
<script lang="ts">
  import GradeChip from '$lib/components/GradeChip.svelte';
  import type { ScenarioResult } from '$lib/api/client';

  export let turn: ScenarioResult;
  export let workspaceId: string;
  // 0-based fallback index from the parent's {#each}, used when the backend
  // didn't carry an explicit `position` (single-shot scenarios).
  export let index = 0;

  // Local, friendly timestamp. Falls back to the raw ISO string if the
  // value can't be parsed — never blank, never a crash.
  function fmtTime(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  }

  $: graderResults = turn.grader_results ?? [];
  // `position` is the 0-based turn index when present; otherwise fall back to
  // the render index so the pill is never blank.
  $: turnNumber = (turn.position ?? index) + 1;
  // `run_id` is the link target for the trace; absent only when there's no
  // persisted trace (then "Open trace" is hidden).
  $: traceHref = turn.run_id ? `/${workspaceId}/traces/${turn.run_id}` : null;
</script>

<article class="flex gap-5 rounded-lg border border-border bg-surface px-5 py-4">
  <!-- Left rail: position + grade, the at-a-glance column. -->
  <div class="flex w-14 shrink-0 flex-col items-center gap-2 pt-0.5">
    <span
      class="inline-flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface-2 font-mono text-xs text-text-2"
      data-numeric
      title={`Turn ${turnNumber}`}
    >
      {turnNumber}
    </span>
    <GradeChip grade={turn.label ?? null} />
  </div>

  <!-- Body. -->
  <div class="min-w-0 flex-1">
    <div class="mb-3 flex items-baseline justify-between gap-3">
      {#if turn.started_at}
        <time class="font-mono text-xs text-text-3" datetime={turn.started_at}>
          {fmtTime(turn.started_at)}
        </time>
      {:else}
        <span class="text-xs text-text-3">{turn.case_name ?? turn.case_id}</span>
      {/if}
      {#if traceHref}
        <a
          href={traceHref}
          class="group inline-flex items-center gap-1.5 text-xs text-text-3 hover:text-text-1 transition-colors"
        >
          <span>Open trace</span>
          <span class="shrink-0" aria-hidden="true">→</span>
        </a>
      {/if}
    </div>

    {#if turn.message}
      <p class="mb-3 whitespace-pre-wrap text-sm text-text-1">{turn.message}</p>
    {/if}

    {#if graderResults.length > 0}
      <details class="group">
        <summary
          class="flex list-none items-center gap-1.5 text-xs uppercase tracking-wide text-text-3 hover:text-text-1 cursor-pointer"
        >
          <span class="group-open:rotate-90 transition-transform" aria-hidden="true">›</span>
          Grader results
          <span class="font-mono normal-case text-text-3" data-numeric
            >· {graderResults.length}</span
          >
        </summary>
        <pre
          class="mt-2 overflow-x-auto rounded-lg border border-border bg-surface-2/40 p-4 font-mono text-xs">{JSON.stringify(
            graderResults,
            null,
            2
          )}</pre>
      </details>
    {:else}
      <div class="text-xs text-text-3">No grader results for this turn.</div>
    {/if}
  </div>
</article>

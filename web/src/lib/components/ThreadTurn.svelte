<!--
  One turn in a thread conversation.

  Layout: a left rail carries the at-a-glance signals (position pill +
  GradeChip), the body carries context (timestamp), a quiet escalation
  ("Open trace →" to zoom into this turn's full span tree), and a
  collapsible for the raw grader results. Designed to read top-to-bottom
  chronologically with calm, generous spacing.
-->
<script lang="ts">
  import CopyableId from '$lib/components/CopyableId.svelte';
  import GradeChip from '$lib/components/GradeChip.svelte';
  import type { ThreadTurn } from '$lib/api/client';

  export let turn: ThreadTurn;
  export let workspaceId: string;

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
</script>

<article class="flex gap-5 rounded-lg border border-border bg-surface px-5 py-4">
  <!-- Left rail: position + grade, the at-a-glance column. -->
  <div class="flex w-14 shrink-0 flex-col items-center gap-2 pt-0.5">
    <span
      class="inline-flex h-7 w-7 items-center justify-center rounded-full border border-border bg-surface-2 font-mono text-xs text-text-2"
      data-numeric
      title={`Turn ${turn.position + 1}`}
    >
      {turn.position + 1}
    </span>
    <GradeChip grade={turn.primary_grade} />
  </div>

  <!-- Body. -->
  <div class="min-w-0 flex-1">
    <div class="mb-3 flex items-baseline justify-between gap-3">
      <time class="font-mono text-xs text-text-3" datetime={turn.started_at}>
        {fmtTime(turn.started_at)}
      </time>
      <a
        href={`/${workspaceId}/traces/${turn.run_id}`}
        class="group inline-flex items-center gap-1.5 text-xs text-text-3 hover:text-text-1 transition-colors"
      >
        <span>Open trace</span>
        <span class="shrink-0" aria-hidden="true">→</span>
      </a>
    </div>

    {#if graderResults.length > 0}
      <details class="group">
        <summary
          class="flex list-none items-center gap-1.5 text-xs uppercase tracking-wide text-text-3 hover:text-text-1 cursor-pointer"
        >
          <span class="group-open:rotate-90 transition-transform" aria-hidden="true">›</span>
          Grader results
          <span class="font-mono normal-case text-text-3" data-numeric>· {graderResults.length}</span>
        </summary>
        <pre
          class="mt-2 overflow-x-auto rounded-lg border border-border bg-surface-2/40 p-4 font-mono text-xs"
        >{JSON.stringify(graderResults, null, 2)}</pre>
      </details>
    {:else}
      <div class="text-xs text-text-3">No grader results for this turn.</div>
    {/if}
  </div>
</article>

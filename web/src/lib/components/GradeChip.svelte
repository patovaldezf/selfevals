<!--
  At-a-glance signal for a grader LABEL (pass/fail/partial/error/skipped).

  NOT to be confused with DecisionBadge, which maps experiment DECISION
  outcomes (keep_candidate/reject/...). A grade is the verdict a grader
  emits on a single turn; reusing DecisionBadge here would fall through to
  its grey "—" for every grade — a silent miscolor.

  Tone comes from viz/tones.ts (token-backed, so pass reads green and fail red
  in both light and dark); the shape is the shared Pill primitive.
-->
<script lang="ts">
  import Pill from './ui/Pill.svelte';
  import type { Tone } from '$lib/viz/tones';

  let { grade }: { grade: string | null } = $props();

  const meta: Record<string, { tone: Tone; label: string }> = {
    pass: { tone: 'positive', label: 'pass' },
    fail: { tone: 'negative', label: 'fail' },
    partial: { tone: 'caution', label: 'partial' },
    error: { tone: 'negative', label: 'error' },
    skipped: { tone: 'neutral', label: 'skipped' }
  };

  const entry = $derived(grade ? meta[grade] : null);
</script>

{#if entry}
  <Pill tone={entry.tone}>{entry.label}</Pill>
{:else if grade}
  <!-- Unknown label: show it verbatim rather than swallowing it. -->
  <Pill tone="neutral">{grade}</Pill>
{:else}
  <span class="text-text-3 text-xs">—</span>
{/if}

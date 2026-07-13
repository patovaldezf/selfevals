<!--
  DecisionBadge — maps an experiment DECISION outcome
  (keep_candidate/reject/revert/...) to a short label + semantic tone. Tone
  comes from viz/tones.ts (token-backed, dark-mode-safe); the shape is Pill.

  NOT to be confused with GradeChip, which colours a grader's per-turn verdict.
-->
<script lang="ts">
  import Pill from './ui/Pill.svelte';
  import type { Tone } from '$lib/viz/tones';

  let { outcome }: { outcome: string | null } = $props();

  const meta: Record<string, { tone: Tone; label: string }> = {
    keep_candidate: { tone: 'positive', label: 'keep' },
    reject: { tone: 'neutral', label: 'reject' },
    revert: { tone: 'negative', label: 'revert' },
    feature_flag: { tone: 'info', label: 'flag' },
    investigate: { tone: 'caution', label: 'investigate' },
    require_tradeoff_review: { tone: 'caution', label: 'tradeoff' },
    spawn_subexperiment: { tone: 'info', label: 'subexp' }
  };

  const entry = $derived(outcome ? meta[outcome] : null);
</script>

{#if entry}
  <Pill tone={entry.tone}>{entry.label}</Pill>
{:else}
  <span class="text-text-3 text-xs">—</span>
{/if}

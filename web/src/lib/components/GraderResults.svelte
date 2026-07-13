<!--
  GraderResults — the raw per-grader verdicts behind a case's aggregate label.

  A ScenarioResult's `label` is the rolled-up answer ("pass"); this shows the
  work: each grader that ran, its own label + score + reason, and its nested
  `breakdown` on demand. This is the detail the Results tab flattens away — the
  "why did THIS grader say fail" a reader opens a case to find.

  Tone comes from viz/tones.ts (token-backed, dark-safe). A `reason_pointer`
  (oss://) means the full reasoning was too big to inline; we surface it via
  PayloadViewer so the reader can pull it without leaving the drawer.
-->
<script lang="ts">
  import type { GraderResult } from '$lib/api/client';
  import type { Tone } from '$lib/viz/tones';
  import Pill from './ui/Pill.svelte';
  import PointerField from './PointerField.svelte';

  let {
    results
  }: {
    results: GraderResult[];
  } = $props();

  function toneForLabel(label: string | null | undefined): Tone {
    switch ((label ?? '').toLowerCase()) {
      case 'pass':
        return 'positive';
      case 'fail':
      case 'error':
        return 'negative';
      case 'partial':
        return 'caution';
      default:
        return 'neutral';
    }
  }

  function fmtScore(score: number | null | undefined): string | null {
    if (score == null || Number.isNaN(score)) return null;
    return Number.isInteger(score) ? `${score}` : score.toFixed(3).replace(/\.?0+$/, '');
  }
</script>

{#if results.length}
  <div class="graders">
    {#each results as g (g.grader)}
      <details class="grader">
        <summary class="grader-head">
          <span class="chevron" aria-hidden="true">▸</span>
          <span class="grader-name font-mono">{g.grader}</span>
          <Pill tone={toneForLabel(g.label)}>{g.label ?? '—'}</Pill>
          {#if fmtScore(g.score) !== null}
            <span class="score font-mono" data-numeric>{fmtScore(g.score)}</span>
          {/if}
          {#if g.confidence != null}
            <span class="confidence font-mono" data-numeric title="confidence">
              conf {g.confidence.toFixed(2)}
            </span>
          {/if}
        </summary>

        <div class="grader-body">
          {#if g.reason}
            <p class="reason">{g.reason}</p>
          {/if}
          {#if g.reason_pointer}
            <PointerField pointer={g.reason_pointer} label="full reasoning" />
          {/if}
          {#if g.failure_modes?.length}
            <div class="modes">
              {#each g.failure_modes as m}
                <Pill tone="negative" title="failure mode">{m}</Pill>
              {/each}
            </div>
          {/if}
          {#if g.breakdown}
            <details class="breakdown">
              <summary>breakdown</summary>
              <pre class="raw">{JSON.stringify(g.breakdown, null, 2)}</pre>
            </details>
          {/if}
        </div>
      </details>
    {/each}
  </div>
{/if}

<style>
  .graders {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .grader {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    overflow: hidden;
  }
  .grader-head {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.5rem 0.7rem;
    cursor: pointer;
    list-style: none;
    user-select: none;
  }
  .grader-head::-webkit-details-marker {
    display: none;
  }
  .chevron {
    color: var(--color-text-3);
    font-size: 0.7rem;
    transition: transform var(--dur-fast) var(--ease-out);
  }
  .grader[open] .chevron {
    transform: rotate(90deg);
  }
  .grader-name {
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .score {
    margin-left: auto;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .confidence {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .grader-body {
    padding: 0 0.7rem 0.7rem;
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
  }
  .reason {
    font-size: var(--text-sm);
    line-height: var(--leading-snug);
    color: var(--color-text-2);
    overflow-wrap: anywhere;
  }
  .modes {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .breakdown summary {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    cursor: pointer;
  }
  .breakdown summary:hover {
    color: var(--color-text-1);
  }
  .raw {
    margin-top: 0.4rem;
    padding: 0.6rem;
    border-radius: var(--radius-sm);
    background: var(--color-surface-2);
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    line-height: 1.5;
    color: var(--color-text-2);
    overflow-x: auto;
  }
</style>

<!--
  CompareVerdict — the answer to "which iteration is better", read in one glance.

  Compare is a decision screen: the verdict is not one row among many, it IS the
  screen. So the delta reads large and coloured by whether it's an improvement,
  the winner is named, A→B values sit under it, and the honest holdout caveat
  never masquerades as a real number. Promote is the action the whole screen
  builds toward — optimistic, with the parent owning the actual mutation.

  Colour comes from the delta language (viz/thresholds via deltaLevel); a tie or
  incomparable pair reads neutral, never a fake green. Runes.
-->
<script lang="ts">
  import type { CompareRecommendation } from '$lib/api/client';
  import type { ThresholdDirection } from '$lib/viz/thresholds';
  import { deltaLevel, levelColor } from '$lib/viz/thresholds';
  import { fmtNumber, fmtDelta } from '$lib/viz/format';
  import Button from './ui/Button.svelte';
  import Pill from './ui/Pill.svelte';
  import Icon from './ui/Icon.svelte';
  import { ArrowRight, TrendingUp, TrendingDown, Minus } from 'lucide-svelte';

  let {
    recommendation,
    holdoutStatus,
    targetDirection,
    canPromote = false,
    promoting = false,
    onPromote
  }: {
    recommendation: CompareRecommendation;
    holdoutStatus: string;
    targetDirection: ThresholdDirection;
    canPromote?: boolean;
    promoting?: boolean;
    onPromote?: () => void;
  } = $props();

  const r = $derived(recommendation);
  const isWinner = $derived(r.kind === 'winner');
  const level = $derived(isWinner ? deltaLevel(r.delta, targetDirection) : 'neutral');
  const accent = $derived(level === 'neutral' ? 'var(--color-text-2)' : levelColor(level));
  const arrow = $derived(
    !isWinner || r.delta == null || r.delta === 0
      ? Minus
      : r.delta > 0 === (targetDirection === 'higher')
        ? TrendingUp
        : TrendingDown
  );

  const headline = $derived.by(() => {
    switch (r.kind) {
      case 'winner':
        return `${r.winner} wins`;
      case 'tie':
        return 'Too close to call';
      case 'different_metric':
        return 'Not comparable';
      default:
        return 'No verdict';
    }
  });

  const subline = $derived.by(() => {
    switch (r.kind) {
      case 'tie':
        return `Both land at ${fmtNumber(r.a_value)} on ${r.metric_name}. Decide on guardrails or failure modes.`;
      case 'different_metric':
        return `A optimizes ${r.a_metric_name}, B optimizes ${r.b_metric_name}.`;
      case 'none':
        return 'No primary metric to compare.';
      default:
        return null;
    }
  });
</script>

<div class="verdict" style:--accent={accent} class:verdict-winner={isWinner}>
  <div class="verdict-main">
    <div class="lead">
      <div class="headline">{headline}</div>
      {#if isWinner && r.metric_name}
        <div class="metric-name font-mono">{r.metric_name}</div>
      {/if}
    </div>

    {#if isWinner}
      <div class="delta-block">
        <span class="delta" style:color={accent}>
          <Icon icon={arrow} size={22} strokeWidth={2.5} />
          <span class="delta-num" data-numeric>{fmtDelta(r.delta)}</span>
        </span>
        <div class="ab font-mono" data-numeric>
          {fmtNumber(r.a_value)}
          <Icon icon={ArrowRight} size={12} />
          {fmtNumber(r.b_value)}
        </div>
      </div>
    {/if}

    {#if canPromote && isWinner}
      <div class="cta">
        <Button variant="primary" size="sm" loading={promoting} on:click={() => onPromote?.()}>
          Promote {r.winner}
        </Button>
      </div>
    {/if}
  </div>

  {#if subline}
    <p class="subline">{subline}</p>
  {/if}

  {#if isWinner && r.new_failure_modes.length > 0}
    <div class="new-modes">
      <span class="new-modes-label">New failure modes in the winner</span>
      <div class="new-modes-list">
        {#each r.new_failure_modes as m}
          <Pill tone="negative">{m}</Pill>
        {/each}
      </div>
    </div>
  {/if}

  <div class="holdout" title="The iteration ledger carries no held-out split classification yet.">
    {holdoutStatus === 'unavailable'
      ? 'Valid on optimization set · Holdout not yet tracked'
      : `Holdout: ${holdoutStatus}`}
  </div>
</div>

<style>
  .verdict {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
    padding: 1.25rem 1.4rem;
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
  }
  /* A winner tints its left edge with the delta colour — the screen's answer
     reads before you parse a single number. */
  .verdict-winner {
    border-left: 3px solid var(--accent);
  }
  .verdict-main {
    display: flex;
    align-items: center;
    gap: 2rem;
    flex-wrap: wrap;
  }
  .lead {
    min-width: 0;
  }
  .headline {
    font-size: var(--text-lg);
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--color-text-1);
    line-height: var(--leading-tight);
  }
  .metric-name {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-top: 0.15rem;
  }
  .delta-block {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
  }
  .delta {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
  }
  .delta-num {
    font-family: var(--font-mono);
    font-size: var(--text-2xl);
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: 1;
  }
  .ab {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .cta {
    margin-left: auto;
  }
  .subline {
    font-size: var(--text-sm);
    color: var(--color-text-2);
    line-height: var(--leading-snug);
    max-width: 42rem;
  }
  .new-modes {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .new-modes-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .new-modes-list {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .holdout {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    padding-top: 0.75rem;
    border-top: 1px dashed var(--color-border);
  }
</style>

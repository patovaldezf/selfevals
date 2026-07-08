<!--
  AnalyticsHeader: the optimization at a glance for an experiment.

  The line is the primary metric climbing across iterations, with the target
  drawn in; the ring is where the best iteration landed against that target.
  Purely presentational — no state, no fetch. Green/amber/red is derived via
  the shared threshold language, never hardcoded.
-->
<script lang="ts">
  import { LineChart, StatRing } from '$lib/components/charts';
  import type { ThresholdDirection } from '$lib/viz/thresholds';

  export let primaryMetric: string;
  export let targetOperator: string;
  export let targetValue: number;
  export let targetDirection: ThresholdDirection;
  export let accuracyPoints: { x: number; y: number }[];
  export let bestValue: number | null;
  export let iterationCount: number;
  export let maxIterations: number;
</script>

<section class="analytics mb-10">
  <div class="analytics-chart card">
    <div class="analytics-head">
      <div>
        <div class="analytics-eyebrow">Primary metric over iterations</div>
        <div class="analytics-metric mono">{primaryMetric}</div>
      </div>
      <div class="analytics-target mono">
        target {targetOperator}
        {targetValue}
      </div>
    </div>
    {#if accuracyPoints.length > 0}
      <LineChart
        points={accuracyPoints}
        height={150}
        format="percent"
        threshold={{ target: targetValue, direction: targetDirection }}
      />
    {:else}
      <div class="analytics-empty">No completed iterations yet.</div>
    {/if}
  </div>

  <div class="analytics-side">
    <div class="card analytics-ring">
      <StatRing
        value={bestValue}
        threshold={{ target: targetValue, direction: targetDirection }}
        label="Best"
        size={84}
      />
    </div>
    <div class="card analytics-stat">
      <span class="analytics-stat-label">Iterations</span>
      <span class="analytics-stat-value mono" data-numeric>
        {iterationCount}<span class="analytics-stat-of">/{maxIterations}</span>
      </span>
    </div>
  </div>
</section>

<style>
  .analytics {
    display: grid;
    grid-template-columns: 1fr 200px;
    gap: 1rem;
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .analytics-chart {
    padding: 1rem 1.2rem 0.6rem;
    min-width: 0;
  }
  .analytics-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .analytics-eyebrow {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .analytics-metric {
    font-size: var(--text-sm);
    color: var(--color-text-1);
    margin-top: 0.15rem;
  }
  .analytics-target {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .analytics-empty {
    padding: 2.5rem 0;
    text-align: center;
    font-size: var(--text-sm);
    color: var(--color-text-3);
  }
  .analytics-side {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .analytics-ring {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0.8rem;
    flex: 1;
  }
  .analytics-stat {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    padding: 0.8rem 1rem;
  }
  .analytics-stat-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .analytics-stat-value {
    font-size: var(--text-xl);
    font-weight: 500;
    line-height: 1;
  }
  .analytics-stat-of {
    color: var(--color-text-3);
    font-size: var(--text-md);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>

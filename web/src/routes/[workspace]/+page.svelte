<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from './$types';
  import { StatRing, Sparkline, DeltaStat } from '$lib/components/charts';
  import Badge from '$lib/components/ui/Badge.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { directionFromOperator, thresholdLevel, levelColor } from '$lib/viz/thresholds';
  import { Layers, Database, ArrowRight, FlaskConical } from 'lucide-svelte';

  export let data: PageData & LayoutData;

  // Anchor points are the primary-metric history per experiment; the last value
  // is "where it landed", the series is its trend. We key by experiment so each
  // row can show its own trajectory and colour it against that experiment's
  // own target (different experiments target different metrics/levels).
  $: anchorByExp = (() => {
    const map = new Map<string, number[]>();
    for (const p of data.anchor) {
      const arr = map.get(p.experiment_id) ?? [];
      arr.push(p.primary_metric_value);
      map.set(p.experiment_id, arr);
    }
    return map;
  })();

  $: sortedExperiments = [...data.experiments].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  );

  $: anchorAll = data.anchor.map((p) => p.primary_metric_value);
  $: anchorLatest = anchorAll.length ? anchorAll[anchorAll.length - 1] : null;

  function last(arr: number[]): number | null {
    return arr.length ? arr[arr.length - 1] : null;
  }
  // Delta of the last point vs the one before — "did this experiment's last
  // iteration move the metric, and which way".
  function trendDelta(arr: number[]): number | null {
    if (arr.length < 2) return null;
    return arr[arr.length - 1] - arr[arr.length - 2];
  }

  function fmtDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
</script>

<svelte:head>
  <title>{data.workspace.name} · selfevals</title>
</svelte:head>

<div class="page">
  <header class="head">
    <div>
      <div class="eyebrow">{data.workspace.slug}</div>
      <h1>{data.workspace.name}</h1>
      {#if data.workspace.description}
        <p class="sub">{data.workspace.description}</p>
      {/if}
    </div>
  </header>

  <!-- Top-line signal: the three numbers that say "how is this workspace doing"
       at a glance. Health is coloured against a 70% bar so a glance reads
       green/amber/red without parsing the digits. -->
  <section class="stats">
    <div class="card stat">
      <span class="stat-label">Experiments</span>
      <span class="stat-value" data-numeric>{data.workspace.experiment_count}</span>
    </div>

    <div class="card stat-health">
      <StatRing
        value={data.workspace.recent_health}
        threshold={{ target: 0.7, direction: 'higher' }}
        label="Recent health"
        size={88}
      />
      <p class="stat-health-note">
        Share of recent cases passing their graders across this workspace.
      </p>
    </div>

    <div class="card anchor">
      <div class="anchor-head">
        <span class="stat-label">Anchor pass@1</span>
        <span class="anchor-runs" data-numeric>
          {anchorAll.length ? `${anchorAll.length} runs` : 'no runs yet'}
        </span>
      </div>
      {#if anchorAll.length}
        <Sparkline
          values={anchorAll}
          width={220}
          height={48}
          endDot
          threshold={{ target: anchorLatest ?? 0, direction: 'higher' }}
        />
      {:else}
        <div class="anchor-empty">Run an experiment to start the anchor trend.</div>
      {/if}
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Recent experiments</h2>
      <span class="count">
        {#if data.experimentsHasMore}
          {sortedExperiments.length} of {data.experimentsTotal}
        {:else}
          {data.experimentsTotal} total
        {/if}
      </span>
    </div>

    {#if sortedExperiments.length === 0}
      <div class="card empty">
        <Icon icon={FlaskConical} size={20} />
        <p class="empty-title">No experiments yet</p>
        <p class="empty-sub">Launch one and its iterations, traces and metrics show up here.</p>
        <code>uv run selfevals run evals/experiments/your-spec.yaml</code>
      </div>
    {:else}
      <div class="card table-wrap">
        <table>
          <thead>
            <tr>
              <th class="l">Experiment</th>
              <th class="l">State</th>
              <th class="l">Primary</th>
              <th class="r">Iterations</th>
              <th class="r">Latest</th>
              <th class="r">Trend</th>
              <th class="r">Updated</th>
            </tr>
          </thead>
          <tbody>
            {#each sortedExperiments as exp (exp.id)}
              {@const trend = anchorByExp.get(exp.id) ?? []}
              {@const dir = directionFromOperator(exp.primary_target.operator)}
              {@const latest = last(trend)}
              {@const lvl = thresholdLevel(latest, {
                target: exp.primary_target.value,
                direction: dir
              })}
              <tr
                on:click={() =>
                  (window.location.href = `/${data.workspace.id}/experiments/${exp.id}`)}
              >
                <td>
                  <span class="exp-name">{exp.name}</span>
                  <span class="exp-goal">{exp.goal}</span>
                </td>
                <td>
                  <span class="state">
                    <StatusDot state={exp.state} />
                    <span class="state-label">{exp.state}</span>
                  </span>
                </td>
                <td class="mono dim">
                  {exp.primary_metric}
                  {exp.primary_target.operator}
                  {exp.primary_target.value}
                </td>
                <td class="r mono" data-numeric>
                  {exp.iteration_count} / {exp.max_iterations}
                </td>
                <td class="r mono" data-numeric>
                  {#if latest !== null}
                    <span style="color: {levelColor(lvl)}">{(latest * 100).toFixed(1)}%</span>
                  {:else}
                    <span class="dim">—</span>
                  {/if}
                </td>
                <td class="r">
                  <span class="trend">
                    <Sparkline
                      values={trend}
                      width={72}
                      height={20}
                      threshold={{ target: exp.primary_target.value, direction: dir }}
                    />
                  </span>
                </td>
                <td class="r mono dim sm">{fmtDate(exp.updated_at)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <section class="nav-cards">
    <a class="card nav-card" href={`/${data.workspace.id}/clusters`}>
      <div class="nav-card-head">
        <Icon icon={Layers} size={18} />
        <h3>Failure clusters</h3>
        <Icon icon={ArrowRight} size={15} class="nav-arrow" />
      </div>
      <p>Failing traces grouped by failure mode, ranked by how often they bite.</p>
    </a>
    <a class="card nav-card" href={`/${data.workspace.id}/datasets`}>
      <div class="nav-card-head">
        <Icon icon={Database} size={18} />
        <h3>Datasets</h3>
        <Icon icon={ArrowRight} size={15} class="nav-arrow" />
      </div>
      <p>Browse cases by taxonomy: level, feature, source, ground truth.</p>
    </a>
  </section>
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 72rem;
    margin: 0 auto;
  }
  .head {
    margin-bottom: 2rem;
  }
  .eyebrow {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 0.35rem;
  }
  h1 {
    font-size: var(--text-2xl);
    font-weight: 600;
    letter-spacing: -0.02em;
    line-height: var(--leading-tight);
  }
  .sub {
    color: var(--color-text-2);
    margin-top: 0.5rem;
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .stats {
    display: grid;
    grid-template-columns: 1fr 1.3fr 1.6fr;
    gap: 1rem;
    margin-bottom: 2.5rem;
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 1.1rem 1.2rem;
    justify-content: center;
  }
  .stat-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .stat-value {
    font-family: var(--font-mono);
    font-size: var(--text-2xl);
    font-weight: 500;
    line-height: 1;
  }
  .stat-health {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 1rem 1.2rem;
  }
  .stat-health-note {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    line-height: var(--leading-snug);
  }
  .anchor {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    padding: 1rem 1.2rem;
    justify-content: center;
  }
  .anchor-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
  }
  .anchor-runs {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .anchor-empty {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .section-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 0.9rem;
  }
  h2 {
    font-size: var(--text-lg);
    font-weight: 600;
  }
  .count {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .table-wrap {
    overflow: hidden;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  thead {
    background: var(--color-surface-2);
  }
  th {
    font-weight: 500;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    padding: 0.6rem 0.9rem;
  }
  th.l {
    text-align: left;
  }
  th.r {
    text-align: right;
  }
  tbody tr {
    border-top: 1px solid var(--color-border);
    cursor: pointer;
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  tbody tr:hover {
    background: var(--color-surface-2);
  }
  td {
    padding: 0.7rem 0.9rem;
    vertical-align: middle;
  }
  td.r {
    text-align: right;
  }
  td.mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: var(--text-xs);
  }
  td.dim,
  .dim {
    color: var(--color-text-3);
  }
  td.sm {
    font-size: var(--text-xs);
  }
  .exp-name {
    display: block;
    font-weight: 500;
    color: var(--color-text-1);
  }
  .exp-goal {
    display: block;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    max-width: 26rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .state {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
  }
  .state-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .trend {
    display: inline-flex;
    color: var(--color-text-3);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
    padding: 3rem 1.5rem;
    text-align: center;
    color: var(--color-text-3);
  }
  .empty-title {
    font-weight: 600;
    color: var(--color-text-1);
  }
  .empty-sub {
    font-size: var(--text-sm);
    color: var(--color-text-2);
  }
  .empty code {
    margin-top: 0.5rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    padding: 0.3rem 0.6rem;
    border-radius: var(--radius-sm);
    background: var(--color-surface-2);
    color: var(--color-text-2);
  }
  .nav-cards {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.25rem;
    margin-top: 2.5rem;
  }
  .nav-card {
    padding: 1.2rem 1.3rem;
    transition:
      border-color var(--dur-fast) var(--ease-out),
      box-shadow var(--dur-fast) var(--ease-out);
  }
  .nav-card:hover {
    border-color: var(--color-border-strong);
    box-shadow: var(--shadow-1);
  }
  .nav-card-head {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin-bottom: 0.5rem;
    color: var(--color-text-2);
  }
  .nav-card-head h3 {
    flex: 1;
    font-weight: 600;
    font-size: var(--text-base);
    color: var(--color-text-1);
  }
  .nav-card :global(.nav-arrow) {
    color: var(--color-text-3);
    transition: transform var(--dur-fast) var(--ease-out);
  }
  .nav-card:hover :global(.nav-arrow) {
    transform: translateX(2px);
    color: var(--color-text-1);
  }
  .nav-card p {
    font-size: var(--text-sm);
    color: var(--color-text-2);
    line-height: var(--leading-snug);
  }
</style>

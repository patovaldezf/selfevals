<script lang="ts">
  /**
   * Failure clusters (§J.6): failing traces grouped by failure mode, ranked by
   * frequency. Triage-first layout — the most painful mode sits on top with the
   * reddest bar, so a glance tells you where to dig. Each row expands to example
   * traces that drill straight into the trace viewer.
   */
  import type { LayoutData } from '../$types';
  import type { PageData } from './$types';
  import Badge from '$lib/components/ui/Badge.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import EmptyState from '$lib/components/EmptyState.svelte';
  import { levelColor, levelSubtle, type ThresholdLevel } from '$lib/viz/thresholds';
  import { Layers, ChevronRight, ArrowRight } from 'lucide-svelte';

  export let data: PageData & LayoutData;

  $: workspaceId = data.workspace.id;
  $: clusters = data.clusters;

  // Which rows are expanded to show example traces. Keyed by failure_mode slug.
  let open: Record<string, boolean> = {};
  function toggle(slug: string) {
    open = { ...open, [slug]: !open[slug] };
  }

  // Severity of a cluster by how often it bites. A failure mode hitting a large
  // share of traces is a fire (red); a long tail is amber; rare is quiet. The
  // bands are deliberately coarse — this is triage, not a precise gauge.
  function severity(rate: number): ThresholdLevel {
    if (rate >= 0.25) return 'bad';
    if (rate >= 0.1) return 'warn';
    return 'neutral';
  }

  // Map the taxonomy status to a Badge tone (Badge already knows the colours).
  function statusTone(status: string): 'candidate' | 'official' | 'retired' | 'neutral' {
    if (status === 'candidate' || status === 'official' || status === 'retired') return status;
    return 'neutral';
  }

  function pct(rate: number): string {
    return `${(rate * 100).toFixed(1)}%`;
  }
</script>

<svelte:head>
  <title>Clusters · {data.workspace.name}</title>
</svelte:head>

<div class="page">
  <header class="head">
    <h1>Failure clusters</h1>
    <p class="sub">
      Failing traces grouped by failure mode, ranked by how often they bite. Open a cluster to drill
      into the traces behind it.
    </p>
  </header>

  {#if !clusters.ok}
    <div class="card notice">Couldn't load clusters. {clusters.error}</div>
  {:else if clusters.value.items.length === 0}
    <EmptyState
      icon="◇"
      title="No failures clustered yet"
      description="Once runs produce failing traces tagged with failure modes, they group here by mode. Run an experiment, then come back."
    />
  {:else}
    <div class="summary mono" data-numeric>
      {clusters.value.total} failing grades across {clusters.value.items.length} cluster{clusters
        .value.items.length === 1
        ? ''
        : 's'}
    </div>

    <div class="list">
      {#each clusters.value.items as c (c.failure_mode)}
        {@const lvl = severity(c.rate)}
        {@const isOpen = open[c.failure_mode] ?? false}
        <div class="cluster card">
          <button class="row" on:click={() => toggle(c.failure_mode)} aria-expanded={isOpen}>
            <span class="caret" class:open={isOpen} aria-hidden="true">
              <Icon icon={ChevronRight} size={15} />
            </span>
            <span class="title-cell">
              <span class="name">{c.title ?? c.failure_mode}</span>
              {#if c.title}<span class="slug mono">{c.failure_mode}</span>{/if}
            </span>
            <Badge tone={statusTone(c.status)} size="sm">{c.status}</Badge>
            <span class="bar-wrap" aria-hidden="true">
              <span
                class="bar"
                style="width: {Math.max(4, c.rate * 100)}%; background: {levelColor(lvl)};"
              ></span>
            </span>
            <span class="count mono" data-numeric>{c.count}</span>
            <span
              class="rate mono"
              data-numeric
              style="color: {lvl === 'neutral' ? 'var(--color-text-3)' : levelColor(lvl)};"
            >
              {pct(c.rate)}
            </span>
          </button>

          {#if isOpen}
            <div class="examples">
              {#if c.failure_mode_id}
                <a class="taxo-link" href={`/${workspaceId}/failure-modes`}>
                  View in taxonomy
                  <Icon icon={ArrowRight} size={13} />
                </a>
              {:else}
                <span class="taxo-link dim">Not in taxonomy yet (candidate)</span>
              {/if}
              {#if c.examples.length}
                <ul>
                  {#each c.examples as ex}
                    <li>
                      <a class="trace-link mono" href={`/${workspaceId}/traces/${ex.run_id}`}>
                        {ex.run_id}
                        <Icon icon={ArrowRight} size={12} />
                      </a>
                    </li>
                  {/each}
                </ul>
                {#if c.examples.length < c.count}
                  <p class="more">+ {c.count - c.examples.length} more</p>
                {/if}
              {:else}
                <p class="more">No example traces recorded.</p>
              {/if}
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 64rem;
    margin: 0 auto;
  }
  .head {
    margin-bottom: 1.5rem;
  }
  h1 {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.02em;
  }
  .sub {
    color: var(--color-text-2);
    margin-top: 0.4rem;
    font-size: var(--text-sm);
    max-width: 42rem;
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .notice {
    padding: 2rem 1.5rem;
    text-align: center;
    color: var(--color-text-2);
    font-size: var(--text-sm);
  }
  .summary {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 0.9rem;
  }
  .list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .cluster {
    overflow: hidden;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    width: 100%;
    padding: 0.7rem 0.9rem;
    text-align: left;
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  .row:hover {
    background: var(--color-surface-2);
  }
  .caret {
    display: inline-flex;
    color: var(--color-text-3);
    transition: transform var(--dur-base) var(--ease-out);
  }
  .caret.open {
    transform: rotate(90deg);
  }
  .title-cell {
    min-width: 0;
    flex: 1;
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }
  .name {
    font-weight: 500;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .slug {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .dim {
    color: var(--color-text-3);
  }
  .bar-wrap {
    flex-shrink: 0;
    width: 7rem;
    height: 0.4rem;
    border-radius: 999px;
    background: var(--color-surface-2);
    overflow: hidden;
  }
  .bar {
    display: block;
    height: 100%;
    border-radius: 999px;
    transition: width var(--dur-slow) var(--ease-out);
  }
  .count {
    flex-shrink: 0;
    width: 2.5rem;
    text-align: right;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .rate {
    flex-shrink: 0;
    width: 3.5rem;
    text-align: right;
    font-size: var(--text-xs);
  }
  .examples {
    padding: 0.5rem 0.9rem 0.8rem 2.5rem;
    border-top: 1px solid var(--color-border);
    background: var(--color-surface-2);
  }
  .taxo-link {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: var(--text-xs);
    margin-bottom: 0.4rem;
  }
  a.taxo-link {
    color: var(--color-text-2);
    text-underline-offset: 2px;
  }
  a.taxo-link:hover {
    color: var(--color-text-1);
  }
  .examples ul {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .trace-link {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
    text-underline-offset: 2px;
  }
  .trace-link:hover {
    color: var(--color-text-1);
    text-decoration: underline;
  }
  .more {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
    margin-top: 0.3rem;
  }
</style>

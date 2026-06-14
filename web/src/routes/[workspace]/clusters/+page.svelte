<script lang="ts">
  /**
   * Failure clusters (§J.6): failing traces grouped by failure mode, ranked by
   * frequency. v1 clusters by the stable taxonomy slug — each row links to the
   * mode in the taxonomy and expands to example traces that drill straight into
   * the trace viewer. Replaces the former mock placeholder.
   */
  import EmptyState from '$lib/components/EmptyState.svelte';
  import type { LayoutData } from '../$types';
  import type { PageData } from './$types';

  export let data: PageData & LayoutData;

  $: workspaceId = data.workspace.id;
  $: clusters = data.clusters;

  // Which rows are expanded to show example traces. Keyed by failure_mode slug.
  let open: Record<string, boolean> = {};
  function toggle(slug: string) {
    open = { ...open, [slug]: !open[slug] };
  }

  const STATUS_TONE: Record<string, string> = {
    candidate: 'tone-candidate',
    official: 'tone-official',
    retired: 'tone-retired',
    unknown: 'tone-unknown'
  };

  function pct(rate: number): string {
    return `${(rate * 100).toFixed(1)}%`;
  }
</script>

<svelte:head>
  <title>Clusters · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-5xl mx-auto">
  <header class="mb-8">
    <h1 class="text-2xl font-semibold tracking-tight">Failure clusters</h1>
    <p class="text-text-2 mt-1.5 text-sm">
      Failing traces grouped by failure mode, ranked by how often they bite. Open a cluster to drill
      into the traces behind it.
    </p>
  </header>

  {#if !clusters.ok}
    <div class="rounded-lg border border-border bg-surface px-6 py-8 text-center text-text-2">
      Couldn't load clusters. {clusters.error}
    </div>
  {:else if clusters.value.items.length === 0}
    <EmptyState
      title="No failures clustered yet"
      description="Once runs produce failing traces tagged with failure modes, they group here by mode. Run an experiment, then come back."
    />
  {:else}
    <div class="mb-4 text-xs text-text-3 font-mono" data-numeric>
      {clusters.value.total} failing grades across {clusters.value.items.length} cluster{clusters
        .value.items.length === 1
        ? ''
        : 's'}
    </div>

    <div class="space-y-2">
      {#each clusters.value.items as c (c.failure_mode)}
        <div class="cluster">
          <button class="row" on:click={() => toggle(c.failure_mode)} aria-expanded={open[c.failure_mode] ?? false}>
            <span class="caret" aria-hidden="true">{open[c.failure_mode] ? '▾' : '▸'}</span>
            <span class="min-w-0 flex-1">
              <span class="name">{c.title ?? c.failure_mode}</span>
              {#if c.title}<span class="slug font-mono">{c.failure_mode}</span>{/if}
            </span>
            <span class="status-pill {STATUS_TONE[c.status] ?? 'tone-unknown'}">{c.status}</span>
            <span class="bar-wrap" aria-hidden="true">
              <span class="bar" style="width: {Math.max(4, c.rate * 100)}%"></span>
            </span>
            <span class="count font-mono" data-numeric>{c.count}</span>
            <span class="rate font-mono text-text-3" data-numeric>{pct(c.rate)}</span>
          </button>

          {#if open[c.failure_mode]}
            <div class="examples">
              {#if c.failure_mode_id}
                <a class="taxo-link" href={`/${workspaceId}/failure-modes`}>View in taxonomy →</a>
              {:else}
                <span class="taxo-link text-text-3">Not in taxonomy yet (candidate)</span>
              {/if}
              {#if c.examples.length}
                <ul>
                  {#each c.examples as ex}
                    <li>
                      <a class="trace-link font-mono" href={`/${workspaceId}/traces/${ex.run_id}`}>
                        {ex.run_id} →
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
  .cluster {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    overflow: hidden;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    width: 100%;
    padding: 0.7rem 0.9rem;
    text-align: left;
    transition: background 0.12s ease;
  }
  .row:hover {
    background: var(--color-surface-2);
  }
  .caret {
    color: var(--color-text-3);
    font-size: 0.7rem;
    width: 0.8rem;
  }
  .name {
    font-weight: 500;
    font-size: 0.875rem;
  }
  .slug {
    margin-left: 0.5rem;
    font-size: 0.7rem;
    color: var(--color-text-3);
  }
  .status-pill {
    flex-shrink: 0;
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 500;
    text-transform: capitalize;
    background: var(--color-surface-2);
    color: var(--color-text-2);
  }
  .tone-candidate {
    color: var(--color-warning);
    background: color-mix(in srgb, var(--color-warning) 12%, transparent);
  }
  .tone-official {
    color: var(--color-success);
    background: color-mix(in srgb, var(--color-success) 12%, transparent);
  }
  .tone-retired,
  .tone-unknown {
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
    background: color-mix(in srgb, var(--color-danger) 55%, transparent);
  }
  .count {
    flex-shrink: 0;
    width: 2.5rem;
    text-align: right;
    font-size: 0.8rem;
  }
  .rate {
    flex-shrink: 0;
    width: 3.5rem;
    text-align: right;
    font-size: 0.75rem;
  }
  .examples {
    padding: 0.5rem 0.9rem 0.8rem 2.45rem;
    border-top: 1px solid var(--color-border);
    background: color-mix(in srgb, var(--color-surface-2) 50%, transparent);
  }
  .taxo-link {
    display: inline-block;
    font-size: 0.75rem;
    margin-bottom: 0.4rem;
  }
  a.taxo-link {
    color: var(--color-text-2);
    text-underline-offset: 2px;
  }
  a.taxo-link:hover {
    color: var(--color-text-1);
    text-decoration: underline;
  }
  .examples ul {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .trace-link {
    font-size: 0.75rem;
    color: var(--color-text-2);
    text-underline-offset: 2px;
  }
  .trace-link:hover {
    color: var(--color-text-1);
    text-decoration: underline;
  }
  .more {
    font-size: 0.7rem;
    color: var(--color-text-3);
    margin-top: 0.3rem;
  }
</style>

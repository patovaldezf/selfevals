<!--
  TraceSidebar: breadcrumb, title, state, meta, promote CTA, and the span tree.

  Presentational; wraps SpanTreeFlat. The Promote button emits `promote` (the
  page owns the modal), span selection emits `select`.
-->
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import CopyableId from '$lib/components/CopyableId.svelte';
  import SpanTreeFlat from '$lib/components/SpanTreeFlat.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { ArrowRight, FlaskConical } from 'lucide-svelte';
  import type { SpanSummary, TraceDetail } from '$lib/api/client';

  export let workspaceId: string;
  export let trace: TraceDetail;
  export let traceTitle: string;
  export let live: boolean;
  export let finalState: string;
  export let spanCount: number;
  export let tree: Map<string | null, SpanSummary[]>;
  export let selected: SpanSummary | null;

  const dispatch = createEventDispatcher<{ select: SpanSummary; promote: void }>();
</script>

<aside class="sidebar">
  <nav class="crumbs" aria-label="Breadcrumb">
    <a href={`/${workspaceId}`}>workspace</a>
    <span aria-hidden="true">/</span>
    <span>trace</span>
  </nav>

  {#if trace.experiment_name && trace.experiment_id}
    <h1 class="title">
      <a href={`/${workspaceId}/experiments/${trace.experiment_id}`}>
        {trace.experiment_name}
      </a>
    </h1>
    {#if trace.iteration !== null}
      <div class="subtitle">Iteration #{trace.iteration}</div>
    {/if}
  {:else}
    <h1 class="title">{traceTitle}</h1>
  {/if}

  <div class="state-row">
    {#if live}
      <span class="state-pill">
        <StatusDot state="running" />
        <span class="state-label">live</span>
      </span>
    {:else}
      <span class="state-pill">
        <StatusDot state={finalState} />
        <span class="state-label">{finalState}</span>
      </span>
    {/if}
    <span class="spans-count" data-numeric>{spanCount} spans</span>
  </div>

  <dl class="meta">
    {#if trace.thread_id}
      <div class="meta-row meta-row-col">
        <dt>thread</dt>
        <dd>
          <a class="thread-link" href={`/${workspaceId}/threads/${trace.thread_id}`}>
            <span>View conversation</span>
            <Icon icon={ArrowRight} size={13} />
          </a>
          <CopyableId id={trace.thread_id} label="thread id" />
          {#if trace.thread_position !== null}
            <span class="turn" data-numeric>turn {trace.thread_position}</span>
          {/if}
        </dd>
      </div>
    {/if}
    <div class="meta-row">
      <dt>run id</dt>
      <dd><CopyableId id={trace.run_id} label="run id" /></dd>
    </div>
    {#if trace.experiment_id}
      <div class="meta-row">
        <dt>experiment id</dt>
        <dd><CopyableId id={trace.experiment_id} label="experiment id" /></dd>
      </div>
    {/if}
  </dl>

  <div class="promote-cta">
    <Button variant="brand" size="sm" on:click={() => dispatch('promote')}>
      <Icon icon={FlaskConical} size={14} />
      Promote to regression case
    </Button>
    <p class="promote-hint">Turn this run into permanent test coverage.</p>
  </div>

  <div class="tree-head">
    <span>Spans</span>
    <span class="tree-hint">↑↓ / j k to move</span>
  </div>
  <SpanTreeFlat {tree} {selected} setSelected={(s) => dispatch('select', s)} />
</aside>

<style>
  .sidebar {
    border-right: 1px solid var(--color-border);
    background: var(--color-surface);
    padding: 1.5rem 1.25rem;
    overflow-y: auto;
  }
  .crumbs {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 0.75rem;
  }
  .crumbs a:hover {
    color: var(--color-text-1);
  }
  .title {
    font-size: var(--text-lg);
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: var(--leading-snug);
  }
  .title a:hover {
    color: var(--color-text-2);
  }
  .subtitle {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-top: 0.2rem;
  }
  .state-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0.9rem 0 1.1rem;
  }
  .state-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.25rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg);
  }
  .state-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
    text-transform: capitalize;
  }
  .spans-count {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .meta {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
    margin-bottom: 1.25rem;
  }
  .meta-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .meta-row-col {
    align-items: flex-start;
  }
  .meta-row dt {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .meta-row-col dd {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.35rem;
    min-width: 0;
  }
  .thread-link {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    color: var(--color-text-2);
    font-size: var(--text-xs);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .thread-link:hover {
    color: var(--color-text-1);
  }
  .turn {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .promote-cta {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.9rem 0;
    margin-bottom: 0.5rem;
    border-top: 1px solid var(--color-border);
    border-bottom: 1px solid var(--color-border);
  }
  .promote-hint {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .tree-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin: 1rem 0 0.5rem;
  }
  .tree-head > span:first-child {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .tree-hint {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
</style>

<!--
  LiveRunHeader: the "watch a run live" moment for an active experiment.

  Self-sufficient — owns both of its own live-data cycles instead of the
  parent page threading their state through props:

  1. Polling (`invalidateAll` every 2.5s) so iterations/state climb without
     a manual refresh — same cadence as ActiveRunsPill. Only runs while
     `isActive` is true.
  2. SSE lookup: while active, polls /runs/active every 3s to find this
     workspace's live run id, then subscribes to its span stream so the
     header shows real motion (span count climbing, last span name) and a
     one-click jump to the trace viewer. Everything tears down on
     complete / when the run leaves the active set / on destroy.

  This is the one component in the experiment page with real teardown
  discipline to get right (two intervals + one SSE handle) — kept as an
  autonomous component rather than split further, since Svelte 4 (no
  runes, used throughout this codebase) has no clean way to share that
  lifecycle via a hook.
-->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { invalidateAll } from '$app/navigation';
  import { api } from '$lib/api/client';
  import { openTraceStream, type StreamHandle } from '$lib/api/sse';
  import { createPoller, type Poller } from '$lib/api/live';
  import { toast } from '$lib/stores/toasts';
  import { CountUp } from '$lib/components/charts';
  import { levelColor, type ThresholdLevel } from '$lib/viz/thresholds';

  export let workspaceId: string;
  export let isActive: boolean;
  export let iterationCount: number;
  export let maxIterations: number;
  export let primaryMetric: string;
  export let bestValue: number | null;
  export let bestLevel: ThresholdLevel;

  // --- Poll so iterations/state climb without a manual refresh -----------
  // Shared poller: pauses on a hidden tab, catches up on refocus.
  let poll: Poller | null = null;

  function startPoll() {
    if (poll) return;
    poll = createPoller(() => void invalidateAll(), 2500);
  }
  function stopPoll() {
    poll?.stop();
    poll = null;
  }
  $: if (isActive) startPoll();
  else stopPoll();
  onDestroy(stopPoll);

  // --- Stream spans from the live run as they land ------------------------
  let liveRunId: string | null = null;
  let liveSpanCount = 0;
  let liveLastSpan: string | null = null;
  let liveStream: StreamHandle | null = null;
  let liveLookup: Poller | null = null;

  async function findLiveRun(): Promise<void> {
    try {
      const { runs } = await api.activeRuns();
      const mine = runs.find((r) => r.workspace_id === workspaceId);
      if (mine && mine.run_id !== liveRunId) attachLive(mine.run_id);
      else if (!mine) detachLive();
    } catch {
      /* transient — keep the last known live state */
    }
  }

  function attachLive(runId: string): void {
    detachLive();
    liveRunId = runId;
    liveSpanCount = 0;
    liveLastSpan = null;
    liveStream = openTraceStream(workspaceId, runId, {
      onSnapshot: (trace) => {
        liveSpanCount = trace.spans?.length ?? 0;
      },
      onSpan: (span) => {
        liveSpanCount += 1;
        liveLastSpan = span.name ?? span.kind ?? 'span';
      },
      onComplete: () => {
        detachLive();
        // The run just finished. Pull the final state in and tell the user,
        // so a completed run doesn't sit looking active until a manual refresh.
        void invalidateAll();
        toast.success('Run complete', 'Results and iterations are up to date.');
      }
    });
  }

  function detachLive(): void {
    liveStream?.close();
    liveStream = null;
    liveRunId = null;
  }

  // Poll for the live run id only while active; the SSE itself is push-based.
  $: if (isActive) startLiveLookup();
  else stopLiveLookup();

  function startLiveLookup(): void {
    if (liveLookup) return;
    liveLookup = createPoller(() => void findLiveRun(), 3000);
  }
  function stopLiveLookup(): void {
    liveLookup?.stop();
    liveLookup = null;
    detachLive();
  }
  onDestroy(stopLiveLookup);
</script>

<div class="live mb-8" class:live-attached={liveRunId}>
  <div class="live-main">
    <span class="live-dot" aria-hidden="true"></span>
    <span class="live-title">Run in progress</span>
    <span class="live-sep" aria-hidden="true">·</span>
    <span class="live-iter mono" data-numeric>
      iteration {iterationCount}<span class="live-iter-of">/{maxIterations}</span>
    </span>
    {#if bestValue !== null}
      <span class="live-sep" aria-hidden="true">·</span>
      <span class="live-metric">
        <span class="live-metric-label">{primaryMetric}</span>
        <span class="live-metric-val" style:color={levelColor(bestLevel)}>
          <CountUp value={bestValue} format="percent" />
        </span>
      </span>
    {/if}
  </div>

  <div class="live-foot">
    {#if liveRunId}
      <span class="live-activity">
        <span class="live-activity-count mono" data-numeric>{liveSpanCount}</span>
        <span class="live-activity-label">span{liveSpanCount === 1 ? '' : 's'}</span>
        {#if liveLastSpan}
          <span class="live-sep" aria-hidden="true">·</span>
          <span class="live-last">{liveLastSpan}</span>
        {/if}
      </span>
      <a class="watch-link" href={`/${workspaceId}/traces/${liveRunId}`}>Watch live →</a>
    {:else}
      <span class="live-waiting">waiting for the run to emit spans…</span>
    {/if}
  </div>

  <!-- Progress of the optimization loop across its iteration budget. -->
  <div class="live-progress" aria-hidden="true">
    <div
      class="live-progress-fill"
      style:width="{Math.min(100, (iterationCount / Math.max(1, maxIterations)) * 100)}%"
    ></div>
  </div>
</div>

<style>
  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.3;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .live-dot,
    .live-dot::after {
      animation: none;
    }
  }

  /* Live header — brand-tinted, the one place the chromatic accent signals
     "this is happening now". */
  .live {
    border: 1px solid color-mix(in srgb, var(--color-brand) 30%, var(--color-border));
    border-radius: var(--radius-lg);
    background: var(--color-brand-subtle);
    overflow: hidden;
  }
  .live-main {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.75rem 1rem 0.5rem;
  }
  .live-dot {
    position: relative;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--color-brand);
    flex-shrink: 0;
  }
  /* A second expanding ring for a richer "live" pulse than a simple fade. */
  .live-dot::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--color-brand);
    animation: live-ping 1.8s ease-out infinite;
  }
  @keyframes live-ping {
    0% {
      transform: scale(1);
      opacity: 0.5;
    }
    100% {
      transform: scale(3);
      opacity: 0;
    }
  }
  .live-title {
    font-weight: 600;
    font-size: var(--text-sm);
    color: var(--color-text-1);
  }
  .live-sep {
    color: var(--color-text-3);
  }
  .live-iter {
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .live-iter-of {
    color: var(--color-text-3);
  }
  .live-metric {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35rem;
  }
  .live-metric-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .live-metric-val {
    font-size: var(--text-sm);
    font-weight: 600;
  }
  .live-foot {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0 1rem 0.7rem;
  }
  .live-activity {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .live-activity-count {
    color: var(--color-text-1);
    font-weight: 500;
  }
  .live-activity-label,
  .live-last {
    color: var(--color-text-3);
  }
  .live-waiting {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    font-style: italic;
  }
  .watch-link {
    margin-left: auto;
    font-size: var(--text-xs);
    font-weight: 500;
    color: var(--color-brand-strong);
    text-underline-offset: 2px;
  }
  .watch-link:hover {
    text-decoration: underline;
  }
  .live-progress {
    height: 3px;
    background: color-mix(in srgb, var(--color-brand) 15%, transparent);
  }
  .live-progress-fill {
    height: 100%;
    background: var(--color-brand);
    transition: width var(--dur-slow) var(--ease-out);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>

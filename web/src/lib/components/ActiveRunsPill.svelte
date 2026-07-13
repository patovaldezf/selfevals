<!--
  ActiveRunsPill — the global "something is running now" affordance in the topbar.

  Compact by default: a pulsing dot + count ("2 live"). Clicking opens a dropdown
  listing each active run with a jump to its live trace. Lives in the horizontal
  topbar, so it stays a pill, not the vertical list it used to be in the sidebar.

  Polls /runs/active through the shared live poller (pauses when the tab is
  hidden). Filtered to the current workspace when one is set. Runes.
-->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { fly } from 'svelte/transition';
  import { api } from '$lib/api/client';
  import { createPoller, type Poller } from '$lib/api/live';
  import { popoverPanel } from '$lib/motion';

  type ActiveRun = { workspace_id: string; run_id: string };

  let { workspaceId = null }: { workspaceId?: string | null } = $props();

  let runs = $state<ActiveRun[]>([]);
  let open = $state(false);
  let poller: Poller | null = null;

  async function fetchActive() {
    try {
      const body = await api.activeRuns();
      runs = body.runs ?? [];
    } catch {
      /* keep last known state; this is ambient, never user-facing */
    }
  }

  // Poll for the lifetime of the component; the poller self-pauses on hidden tab.
  $effect(() => {
    poller = createPoller(fetchActive, 2500);
    return () => poller?.stop();
  });
  onDestroy(() => poller?.stop());

  const visible = $derived(
    workspaceId ? runs.filter((r) => r.workspace_id === workspaceId) : runs
  );

  function close() {
    open = false;
  }
</script>

{#if visible.length > 0}
  <div class="pill-wrap">
    <button
      class="pill"
      onclick={() => (open = !open)}
      aria-expanded={open}
      aria-haspopup="menu"
      title="{visible.length} run{visible.length === 1 ? '' : 's'} live"
    >
      <span class="dot" aria-hidden="true"></span>
      <span class="count" data-numeric>{visible.length}</span>
      <span class="label">live</span>
    </button>

    {#if open}
      <!-- Click-away scrim, transparent, closes the menu. -->
      <button class="scrim" onclick={close} aria-label="Close" tabindex="-1"></button>
      <div class="menu" role="menu" transition:fly={popoverPanel()}>
        <div class="menu-head">Active runs</div>
        {#each visible as r (r.run_id)}
          <a
            class="menu-item"
            role="menuitem"
            href={`/${r.workspace_id}/traces/${r.run_id}`}
            onclick={close}
          >
            <span class="dot" aria-hidden="true"></span>
            <span class="run-id font-mono">{r.run_id}</span>
            <span class="arrow" aria-hidden="true">→</span>
          </a>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  @keyframes live-ping {
    0% {
      transform: scale(1);
      opacity: 0.5;
    }
    100% {
      transform: scale(2.6);
      opacity: 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .dot::after {
      animation: none;
    }
  }
  .pill-wrap {
    position: relative;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.28rem 0.6rem;
    border: 1px solid color-mix(in srgb, var(--color-brand) 30%, var(--color-border));
    border-radius: 999px;
    background: var(--color-brand-subtle);
    color: var(--color-text-1);
    transition: border-color var(--dur-fast) var(--ease-out);
  }
  .pill:hover {
    border-color: var(--color-brand);
  }
  .dot {
    position: relative;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--color-brand);
    flex-shrink: 0;
  }
  .dot::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: var(--color-brand);
    animation: live-ping 1.8s ease-out infinite;
  }
  .count {
    font-size: var(--text-xs);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .label {
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: transparent;
    cursor: default;
  }
  .menu {
    position: absolute;
    top: calc(100% + 0.4rem);
    right: 0;
    z-index: 50;
    min-width: 15rem;
    padding: 0.35rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    box-shadow: var(--shadow-3);
  }
  .menu-head {
    padding: 0.35rem 0.5rem 0.4rem;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .menu-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.5rem;
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  .menu-item:hover {
    background: var(--color-surface-2);
  }
  .run-id {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--color-text-2);
  }
  .arrow {
    color: var(--color-text-3);
  }
  .menu-item:hover .arrow {
    color: var(--color-text-1);
  }
</style>

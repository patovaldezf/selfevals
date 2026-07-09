<!--
  Topbar — the one global header. Sticky across every page: breadcrumb trail on
  the left (derived from the URL, ids swapped for names via crumbLabels), and on
  the right the live-runs pill, ⌘K entry, and theme toggle. This is what makes
  the app feel like one surface instead of 16 pages each with its own header.
-->
<script lang="ts">
  import { page } from '$app/stores';
  import { crumbsFromPath, crumbLabels } from '$lib/nav/breadcrumbs';
  import { paletteOpen } from '$lib/stores/commands';
  import { theme } from '$lib/stores/theme';
  import ActiveRunsPill from './ActiveRunsPill.svelte';
  import Icon from './ui/Icon.svelte';
  import Kbd from './ui/Kbd.svelte';
  import { Search, Sun, Moon } from 'lucide-svelte';

  let { workspaceId = null }: { workspaceId?: string | null } = $props();

  const crumbs = $derived(crumbsFromPath($page.url.pathname, $crumbLabels));
</script>

<header class="topbar">
  <nav class="crumbs" aria-label="Breadcrumb">
    {#each crumbs as crumb, i (crumb.href)}
      {#if i > 0}
        <span class="sep" aria-hidden="true">/</span>
      {/if}
      {#if i === crumbs.length - 1}
        <span class="crumb current" aria-current="page">{crumb.label}</span>
      {:else}
        <a class="crumb" href={crumb.href}>{crumb.label}</a>
      {/if}
    {/each}
  </nav>

  <div class="actions">
    <ActiveRunsPill {workspaceId} />
    <button class="cmdk" onclick={() => paletteOpen.set(true)} aria-label="Open command palette">
      <Icon icon={Search} size={13} />
      <Kbd keys={['⌘', 'K']} />
    </button>
    <button
      class="icon-btn"
      aria-label="Toggle theme"
      title="Toggle theme"
      onclick={() => theme.toggle()}
    >
      <Icon icon={$theme === 'dark' ? Sun : Moon} size={15} />
    </button>
  </div>
</header>

<style>
  .topbar {
    position: sticky;
    top: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    height: 48px;
    padding: 0 1.25rem;
    background: color-mix(in srgb, var(--color-bg) 82%, transparent);
    backdrop-filter: saturate(1.4) blur(8px);
    border-bottom: 1px solid var(--color-border);
  }
  .crumbs {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    min-width: 0;
    font-size: var(--text-sm);
    overflow: hidden;
    white-space: nowrap;
  }
  .crumb {
    color: var(--color-text-3);
    transition: color var(--dur-fast) var(--ease-out);
  }
  a.crumb:hover {
    color: var(--color-text-1);
  }
  .crumb.current {
    color: var(--color-text-1);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .sep {
    color: var(--color-text-3);
    opacity: 0.6;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-shrink: 0;
  }
  .cmdk {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.28rem 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    color: var(--color-text-3);
    transition:
      border-color var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .cmdk:hover {
    border-color: var(--color-border-strong);
    color: var(--color-text-1);
  }
  .icon-btn {
    display: inline-flex;
    padding: 0.35rem;
    border-radius: var(--radius-sm);
    color: var(--color-text-3);
    transition:
      color var(--dur-fast) var(--ease-out),
      background-color var(--dur-fast) var(--ease-out);
  }
  .icon-btn:hover {
    background: var(--color-surface-2);
    color: var(--color-text-1);
  }
</style>

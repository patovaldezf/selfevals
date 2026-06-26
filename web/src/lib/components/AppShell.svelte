<script lang="ts">
  import { page } from '$app/stores';
  import ActiveRunsPill from './ActiveRunsPill.svelte';
  import { theme } from '$lib/stores/theme';
  export let workspaceId: string | null = null;

  $: nav = workspaceId
    ? [
        { href: `/${workspaceId}`, label: 'Overview' },
        { href: `/${workspaceId}/experiments`, label: 'Experiments' },
        { href: `/${workspaceId}/metrics`, label: 'Metrics' },
        { href: `/${workspaceId}/datasets`, label: 'Datasets' },
        { href: `/${workspaceId}/failure-modes`, label: 'Failure modes' },
        { href: `/${workspaceId}/anchor-set`, label: 'Anchor set' },
        { href: `/${workspaceId}/clusters`, label: 'Clusters' }
      ]
    : [{ href: '/', label: 'Workspaces' }];
</script>

<div class="grid min-h-screen grid-cols-[220px_1fr]">
  <aside class="border-r border-border bg-surface px-5 py-7 flex flex-col gap-8">
    <a href="/" class="flex items-center gap-2 font-semibold tracking-tight">
      <span class="inline-block h-2.5 w-2.5 rounded-full bg-accent" aria-hidden="true"></span>
      <span>selfevals</span>
    </a>

    <nav class="flex flex-col gap-1 text-sm">
      {#each nav as item}
        {@const active =
          $page.url.pathname === item.href ||
          (item.href !== '/' && $page.url.pathname.startsWith(item.href + '/'))}
        <a
          href={item.href}
          class="px-3 py-1.5 rounded-md transition-colors {active
            ? 'bg-surface-2 text-text-1 font-medium'
            : 'text-text-2 hover:text-text-1 hover:bg-surface-2'}"
        >
          {item.label}
        </a>
      {/each}
    </nav>

    <div class="mt-auto pt-6 border-t border-border space-y-3">
      <ActiveRunsPill {workspaceId} />
      <div class="flex items-center justify-between">
        <span class="text-xs text-text-3 font-mono">v0.0.1 · localhost</span>
        <button
          class="rounded-md px-1.5 py-1 text-text-3 transition-colors hover:bg-surface-2 hover:text-text-1"
          aria-label="Toggle theme"
          title="Toggle theme"
          on:click={() => theme.toggle()}
        >
          {$theme === 'dark' ? '☀' : '☾'}
        </button>
      </div>
    </div>
  </aside>

  <main class="min-w-0">
    <slot />
  </main>
</div>

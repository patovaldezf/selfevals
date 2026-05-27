<script lang="ts">
  import { page } from '$app/stores';
  import ActiveRunsPill from './ActiveRunsPill.svelte';
  export let workspaceId: string | null = null;

  $: nav = workspaceId
    ? [
        { href: `/${workspaceId}`, label: 'Overview' },
        { href: `/${workspaceId}/experiments`, label: 'Experiments' },
        { href: `/${workspaceId}/anchor-set`, label: 'Anchor set' },
        { href: `/${workspaceId}/clusters`, label: 'Clusters' },
        { href: `/${workspaceId}/datasets`, label: 'Datasets' }
      ]
    : [{ href: '/', label: 'Workspaces' }];
</script>

<div class="grid min-h-screen grid-cols-[220px_1fr]">
  <aside class="border-r border-border bg-surface px-5 py-7 flex flex-col gap-8">
    <a href="/" class="flex items-center gap-2 font-semibold tracking-tight">
      <span
        class="inline-block h-2.5 w-2.5 rounded-full bg-accent"
        aria-hidden="true"
      ></span>
      <span>selfeval</span>
    </a>

    <nav class="flex flex-col gap-1 text-sm">
      {#each nav as item}
        {@const active = $page.url.pathname === item.href || (item.href !== '/' && $page.url.pathname.startsWith(item.href + '/'))}
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
      <div class="text-xs text-text-3 font-mono">v0.0.1 · localhost</div>
    </div>
  </aside>

  <main class="min-w-0">
    <slot />
  </main>
</div>

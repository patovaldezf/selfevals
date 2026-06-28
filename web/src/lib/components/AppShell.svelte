<script lang="ts">
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { onMount, onDestroy } from 'svelte';
  import ActiveRunsPill from './ActiveRunsPill.svelte';
  import Icon from './ui/Icon.svelte';
  import Kbd from './ui/Kbd.svelte';
  import { theme } from '$lib/stores/theme';
  import { commands, paletteOpen } from '$lib/stores/commands';
  import { shortcuts, helpOpen, mountShortcutListener } from '$lib/stores/shortcuts';
  import {
    LayoutDashboard,
    FlaskConical,
    BarChart3,
    Database,
    AlertTriangle,
    Layers,
    Anchor,
    Command as CommandIcon,
    Sun,
    Moon,
    Search
  } from 'lucide-svelte';

  export let workspaceId: string | null = null;

  // Nav is the navigation spine; each item also becomes a ⌘K command and a
  // `g <key>` shortcut so the keyboard reaches everything the sidebar does.
  type NavItem = {
    href: string;
    label: string;
    icon: typeof LayoutDashboard;
    chord?: string;
  };

  $: nav = (
    workspaceId
      ? [
          { href: `/${workspaceId}`, label: 'Overview', icon: LayoutDashboard, chord: 'o' },
          {
            href: `/${workspaceId}/experiments`,
            label: 'Experiments',
            icon: FlaskConical,
            chord: 'e'
          },
          { href: `/${workspaceId}/metrics`, label: 'Metrics', icon: BarChart3, chord: 'm' },
          { href: `/${workspaceId}/datasets`, label: 'Datasets', icon: Database, chord: 'd' },
          {
            href: `/${workspaceId}/failure-modes`,
            label: 'Failure modes',
            icon: AlertTriangle,
            chord: 'f'
          },
          { href: `/${workspaceId}/clusters`, label: 'Clusters', icon: Layers, chord: 'c' },
          { href: `/${workspaceId}/anchor-set`, label: 'Anchor set', icon: Anchor, chord: 'a' }
        ]
      : [{ href: '/', label: 'Workspaces', icon: LayoutDashboard }]
  ) as NavItem[];

  function isActive(href: string): boolean {
    const path = $page.url.pathname;
    return path === href || (href !== '/' && path.startsWith(href + '/'));
  }

  // Register nav as commands + shortcuts whenever the workspace changes; the
  // returned teardown swaps cleanly so stale workspace links never linger.
  let teardownReg: (() => void) | null = null;
  let teardownShort: (() => void) | null = null;
  $: {
    teardownReg?.();
    const unNav = commands.register(
      nav.map((item) => ({
        id: `nav:${item.href}`,
        title: `Go to ${item.label}`,
        group: 'Navigate',
        icon: item.icon,
        shortcut: item.chord ? ['G', item.chord.toUpperCase()] : undefined,
        run: () => goto(item.href)
      }))
    );
    const unTheme = commands.register([
      {
        id: 'theme:toggle',
        title: 'Toggle theme',
        group: 'General',
        icon: $theme === 'dark' ? Sun : Moon,
        run: () => theme.toggle()
      }
    ]);
    const unShort = shortcuts.register([
      ...nav
        .filter((i) => i.chord)
        .map((item) => ({
          keys: ['g', item.chord!],
          label: `Go to ${item.label}`,
          group: 'Navigate',
          run: () => goto(item.href)
        })),
      {
        keys: ['?'],
        label: 'Show keyboard shortcuts',
        group: 'General',
        run: () => helpOpen.update((v) => !v)
      }
    ]);
    teardownReg = () => {
      unNav();
      unTheme();
      unShort();
    };
  }

  onMount(() => {
    teardownShort = mountShortcutListener(() => paletteOpen.update((v) => !v));
  });
  onDestroy(() => {
    teardownReg?.();
    teardownShort?.();
  });
</script>

<div class="shell">
  <aside class="sidebar">
    <a href="/" class="brand">
      <span class="brand-mark" aria-hidden="true"></span>
      <span>selfevals</span>
    </a>

    <button class="cmdk" on:click={() => paletteOpen.set(true)}>
      <Icon icon={Search} size={14} />
      <span class="cmdk-label">Search…</span>
      <Kbd keys={['⌘', 'K']} />
    </button>

    <nav class="nav">
      {#each nav as item (item.href)}
        {@const active = isActive(item.href)}
        <a
          href={item.href}
          class="nav-item"
          class:active
          aria-current={active ? 'page' : undefined}
        >
          <span class="nav-rail" aria-hidden="true"></span>
          <Icon icon={item.icon} size={16} />
          <span>{item.label}</span>
        </a>
      {/each}
    </nav>

    <div class="footer">
      <ActiveRunsPill {workspaceId} />
      <div class="footer-row">
        <span class="version">v0.13 · localhost</span>
        <button
          class="theme-btn"
          aria-label="Toggle theme"
          title="Toggle theme"
          on:click={() => theme.toggle()}
        >
          <Icon icon={$theme === 'dark' ? Sun : Moon} size={15} />
        </button>
      </div>
    </div>
  </aside>

  <main class="main">
    <slot />
  </main>
</div>

<style>
  .shell {
    display: grid;
    grid-template-columns: 232px 1fr;
    min-height: 100vh;
  }
  .sidebar {
    display: flex;
    flex-direction: column;
    gap: 1.1rem;
    padding: 1.1rem 0.75rem 1.25rem;
    border-right: 1px solid var(--color-border);
    background: var(--color-surface);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.15rem 0.5rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--color-text-1);
  }
  .brand-mark {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 50%;
    background: var(--color-brand);
  }
  .cmdk {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.55rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg);
    color: var(--color-text-3);
    transition:
      border-color var(--dur-fast) var(--ease-out),
      background-color var(--dur-fast) var(--ease-out);
  }
  .cmdk:hover {
    border-color: var(--color-border-strong);
    background: var(--color-surface-2);
  }
  .cmdk-label {
    flex: 1;
    text-align: left;
    font-size: var(--text-sm);
  }
  .nav {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
  }
  .nav-item {
    position: relative;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.4rem 0.55rem;
    border-radius: var(--radius-md);
    font-size: var(--text-sm);
    color: var(--color-text-2);
    transition:
      color var(--dur-fast) var(--ease-out),
      background-color var(--dur-fast) var(--ease-out);
  }
  .nav-item :global(svg) {
    color: var(--color-text-3);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .nav-item:hover {
    background: var(--color-surface-2);
    color: var(--color-text-1);
  }
  .nav-item:hover :global(svg) {
    color: var(--color-text-2);
  }
  .nav-item.active {
    background: var(--color-surface-2);
    color: var(--color-text-1);
    font-weight: 500;
  }
  .nav-item.active :global(svg) {
    color: var(--color-brand);
  }
  .nav-rail {
    position: absolute;
    left: -0.75rem;
    top: 50%;
    height: 0;
    width: 2px;
    border-radius: 1px;
    background: var(--color-brand);
    transform: translateY(-50%);
    transition: height var(--dur-base) var(--ease-out);
  }
  .nav-item.active .nav-rail {
    height: 1.1rem;
  }
  .footer {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    padding-top: 0.9rem;
    border-top: 1px solid var(--color-border);
  }
  .footer-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 0.4rem;
  }
  .version {
    font-size: var(--text-2xs);
    font-family: var(--font-mono);
    color: var(--color-text-3);
  }
  .theme-btn {
    display: inline-flex;
    padding: 0.3rem;
    border-radius: var(--radius-sm);
    color: var(--color-text-3);
    transition:
      color var(--dur-fast) var(--ease-out),
      background-color var(--dur-fast) var(--ease-out);
  }
  .theme-btn:hover {
    background: var(--color-surface-2);
    color: var(--color-text-1);
  }
  .main {
    min-width: 0;
  }
</style>

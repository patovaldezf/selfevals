<script lang="ts">
  /** ⌘K command palette — the keyboard-first spine of the app. Fuzzy-filters the
   *  registered command list, groups by `group`, and runs the selected command.
   *  Arrow keys / j-k move; Enter runs; Escape closes. Opens from anywhere via
   *  the global shortcut listener (see stores/shortcuts.ts). Motion is a quick
   *  fade + scale from the top, interruptible and reduced-motion aware. */
  import { tick } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import { commandList, paletteOpen, type Command } from '$lib/stores/commands';
  import Icon from './Icon.svelte';
  import Kbd from './Kbd.svelte';
  import { Search } from 'lucide-svelte';

  let query = '';
  let active = 0;
  let inputEl: HTMLInputElement | null = null;

  // Subsequence fuzzy match: every query char appears in order. Cheap, good
  // enough for a few dozen commands, and forgiving of abbreviations.
  function score(cmd: Command, q: string): number {
    if (!q) return 1;
    const hay = `${cmd.title} ${cmd.group ?? ''} ${cmd.keywords ?? ''}`.toLowerCase();
    const needle = q.toLowerCase();
    let i = 0;
    let gaps = 0;
    for (const ch of hay) {
      if (ch === needle[i]) {
        i++;
        if (i === needle.length) break;
      } else if (i > 0) {
        gaps++;
      }
    }
    if (i < needle.length) return 0;
    // Prefer contiguous, title-leading matches.
    const lead = cmd.title.toLowerCase().startsWith(needle) ? 100 : 0;
    return 1000 + lead - gaps;
  }

  $: filtered = $commandList
    .map((c) => ({ c, s: score(c, query) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .map((x) => x.c);

  // Group preserving the filtered order; "Navigate" floats first when present.
  $: groups = (() => {
    const order: string[] = [];
    const by = new Map<string, Command[]>();
    for (const c of filtered) {
      const g = c.group ?? 'Commands';
      if (!by.has(g)) {
        by.set(g, []);
        order.push(g);
      }
      by.get(g)!.push(c);
    }
    return order.map((g) => ({ group: g, items: by.get(g)! }));
  })();

  $: flat = groups.flatMap((g) => g.items);
  $: if (active >= flat.length) active = Math.max(0, flat.length - 1);

  function indexOf(cmd: Command): number {
    return flat.indexOf(cmd);
  }

  $: if ($paletteOpen) {
    query = query; // keep
    tick().then(() => inputEl?.focus());
  }

  function close() {
    paletteOpen.set(false);
    query = '';
    active = 0;
  }

  function runActive() {
    const cmd = flat[active];
    if (cmd) {
      close();
      cmd.run();
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'ArrowDown' || (e.key === 'n' && e.ctrlKey)) {
      e.preventDefault();
      active = Math.min(active + 1, flat.length - 1);
    } else if (e.key === 'ArrowUp' || (e.key === 'p' && e.ctrlKey)) {
      e.preventDefault();
      active = Math.max(active - 1, 0);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      runActive();
    }
  }
</script>

{#if $paletteOpen}
  <div class="scrim" transition:fade={{ duration: 120 }} on:click={close} aria-hidden="true"></div>
  <div class="positioner">
    <div
      class="palette"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      transition:fly={{ y: -8, duration: 160, opacity: 0 }}
    >
      <div class="search">
        <Icon icon={Search} size={16} class="search-icon" />
        <input
          bind:this={inputEl}
          bind:value={query}
          on:keydown={onKeydown}
          placeholder="Search commands…"
          class="search-input"
          autocomplete="off"
          spellcheck="false"
        />
        <Kbd keys={['Esc']} />
      </div>

      <div class="results" role="listbox" aria-label="Commands">
        {#if flat.length === 0}
          <div class="empty">No matching commands</div>
        {:else}
          {#each groups as g (g.group)}
            <div class="group-label">{g.group}</div>
            {#each g.items as cmd (cmd.id)}
              {@const idx = indexOf(cmd)}
              <button
                type="button"
                class="row"
                class:active={idx === active}
                role="option"
                aria-selected={idx === active}
                on:mousemove={() => (active = idx)}
                on:click={() => {
                  close();
                  cmd.run();
                }}
              >
                {#if cmd.icon}
                  <Icon icon={cmd.icon} size={15} class="row-icon" />
                {:else}
                  <span class="row-icon-spacer"></span>
                {/if}
                <span class="row-title">{cmd.title}</span>
                {#if cmd.shortcut}
                  <Kbd keys={cmd.shortcut} />
                {/if}
              </button>
            {/each}
          {/each}
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(10, 10, 10, 0.32);
    z-index: 60;
  }
  .positioner {
    position: fixed;
    inset: 0;
    z-index: 70;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 14vh;
    pointer-events: none;
  }
  .palette {
    pointer-events: auto;
    width: 100%;
    max-width: 36rem;
    background: var(--color-surface);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-3);
    overflow: hidden;
  }
  .search {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.75rem 0.9rem;
    border-bottom: 1px solid var(--color-border);
  }
  .search :global(.search-icon) {
    color: var(--color-text-3);
    flex-shrink: 0;
  }
  .search-input {
    flex: 1;
    border: none;
    background: transparent;
    outline: none;
    color: var(--color-text-1);
    font-size: var(--text-md);
  }
  .search-input::placeholder {
    color: var(--color-text-3);
  }
  .results {
    max-height: 22rem;
    overflow-y: auto;
    padding: 0.4rem;
  }
  .group-label {
    padding: 0.5rem 0.6rem 0.25rem;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .row {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 0.6rem;
    border-radius: var(--radius-md);
    text-align: left;
    color: var(--color-text-2);
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  .row.active {
    background: var(--color-brand-subtle);
    color: var(--color-text-1);
  }
  .row :global(.row-icon) {
    color: var(--color-text-3);
    flex-shrink: 0;
  }
  .row.active :global(.row-icon) {
    color: var(--color-brand);
  }
  .row-icon-spacer {
    width: 15px;
    flex-shrink: 0;
  }
  .row-title {
    flex: 1;
    font-size: var(--text-sm);
  }
  .empty {
    padding: 1.5rem;
    text-align: center;
    color: var(--color-text-3);
    font-size: var(--text-sm);
  }
</style>

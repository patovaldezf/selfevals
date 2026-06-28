<script lang="ts">
  /** The `?` help sheet — a reference of every registered shortcut, grouped.
   *  Read-only; it reflects whatever the current context has registered, so a
   *  trace page shows its j/k row navigation alongside the global g-sequences. */
  import { fade, scale } from 'svelte/transition';
  import { shortcuts, helpOpen, type Shortcut } from '$lib/stores/shortcuts';
  import Kbd from './Kbd.svelte';

  function close() {
    helpOpen.set(false);
  }

  $: grouped = (() => {
    const order: string[] = [];
    const by = new Map<string, Shortcut[]>();
    for (const s of $shortcuts.values()) {
      const g = s.group ?? 'General';
      if (!by.has(g)) {
        by.set(g, []);
        order.push(g);
      }
      by.get(g)!.push(s);
    }
    return order.map((g) => ({ group: g, items: by.get(g)! }));
  })();

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }
</script>

<svelte:window on:keydown={$helpOpen ? onKeydown : undefined} />

{#if $helpOpen}
  <div class="scrim" transition:fade={{ duration: 120 }} on:click={close} aria-hidden="true"></div>
  <div class="positioner">
    <div
      class="sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      transition:scale={{ start: 0.97, opacity: 0, duration: 160 }}
    >
      <header class="head">
        <h2>Keyboard shortcuts</h2>
        <Kbd keys={['Esc']} />
      </header>
      <div class="body">
        {#each grouped as g (g.group)}
          <section>
            <div class="group">{g.group}</div>
            {#each g.items as s (s.keys.join(' '))}
              <div class="row">
                <span class="label">{s.label}</span>
                <span class="keys">
                  {#each s.keys as k}
                    <Kbd keys={[k.length === 1 ? k.toUpperCase() : k]} />
                  {/each}
                </span>
              </div>
            {/each}
          </section>
        {/each}
        <div class="row">
          <span class="label">Open command palette</span>
          <span class="keys"><Kbd keys={['⌘', 'K']} /></span>
        </div>
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
    align-items: center;
    justify-content: center;
    padding: 1rem;
    pointer-events: none;
  }
  .sheet {
    pointer-events: auto;
    width: 100%;
    max-width: 30rem;
    background: var(--color-surface);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-3);
    overflow: hidden;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.85rem 1.1rem;
    border-bottom: 1px solid var(--color-border);
  }
  .head h2 {
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--color-text-1);
  }
  .body {
    max-height: 60vh;
    overflow-y: auto;
    padding: 0.6rem 1.1rem 1rem;
  }
  section {
    margin-top: 0.7rem;
  }
  .group {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    padding: 0.3rem 0;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.35rem 0;
  }
  .label {
    font-size: var(--text-sm);
    color: var(--color-text-2);
  }
  .keys {
    display: inline-flex;
    gap: 0.25rem;
  }
</style>

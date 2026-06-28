<script lang="ts">
  /** Right-hand slide-in panel for detail without losing the list behind it —
   *  iteration drawers, span detail. Slides on transform (ease-out, --dur-slow)
   *  so it feels like it has weight. Scrim + Escape close, Tab trapped inside,
   *  focus restored on close — same contract as Modal. Body is the default slot;
   *  actions go in `footer`. */
  import { createEventDispatcher, tick } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import { cubicOut } from 'svelte/easing';

  export let open = false;
  export let title: string | null = null;
  export let size: 'sm' | 'md' | 'lg' = 'md';
  export let dismissible = true;

  const dispatch = createEventDispatcher<{ close: void }>();
  let panelEl: HTMLDivElement | null = null;
  let lastFocused: HTMLElement | null = null;

  const widths = { sm: '22rem', md: '32rem', lg: '44rem' };

  function close() {
    if (dismissible) dispatch('close');
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === 'Tab' && panelEl) {
      const f = panelEl.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (f.length === 0) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  $: if (open) {
    lastFocused = typeof document !== 'undefined' ? (document.activeElement as HTMLElement) : null;
    tick().then(() => {
      panelEl?.querySelector<HTMLElement>('input, textarea, select, button, [tabindex]')?.focus();
    });
  } else if (lastFocused) {
    lastFocused.focus();
    lastFocused = null;
  }
</script>

<svelte:window on:keydown={open ? onKeydown : undefined} />

{#if open}
  <div class="scrim" transition:fade={{ duration: 150 }} on:click={close} aria-hidden="true"></div>
  <div
    bind:this={panelEl}
    class="drawer"
    role="dialog"
    aria-modal="true"
    aria-label={title ?? 'Detail'}
    style="width: {widths[size]};"
    transition:fly={{ x: 24, duration: 280, easing: cubicOut, opacity: 0 }}
  >
    {#if title}
      <header class="flex items-center justify-between border-b border-border px-5 py-3.5">
        <h2 class="text-sm font-semibold text-text-1">{title}</h2>
        {#if dismissible}
          <button
            class="text-text-3 transition-colors hover:text-text-1"
            aria-label="Close"
            on:click={close}>✕</button
          >
        {/if}
      </header>
    {/if}
    <div class="body px-5 py-4">
      <slot />
    </div>
    {#if $$slots.footer}
      <footer class="flex justify-end gap-2 border-t border-border px-5 py-3.5">
        <slot name="footer" />
      </footer>
    {/if}
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(10, 10, 10, 0.32);
    z-index: 40;
  }
  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 50;
    max-width: 100vw;
    display: flex;
    flex-direction: column;
    background: var(--color-surface);
    border-left: 1px solid var(--color-border);
    box-shadow: var(--shadow-3);
  }
  .body {
    flex: 1;
    overflow-y: auto;
  }
</style>

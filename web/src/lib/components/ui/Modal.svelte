<script lang="ts">
  /** Centered modal dialog. Closes on scrim click + Escape; restores focus to
   *  the trigger on close and traps Tab inside while open. Motion is a quick
   *  fade+scale (0.96→1, ~160ms, ease-out) — interruptible, reduced-motion aware.
   *  Header via `title`; body is the default slot; actions via the `footer` slot. */
  import { createEventDispatcher, tick } from 'svelte';
  import { fade, scale } from 'svelte/transition';

  export let open = false;
  export let title: string | null = null;
  export let size: 'sm' | 'md' | 'lg' = 'md';
  /** Disable scrim/Escape close while a mutation is in flight. */
  export let dismissible = true;

  const dispatch = createEventDispatcher<{ close: void }>();
  let dialogEl: HTMLDivElement | null = null;
  let lastFocused: HTMLElement | null = null;

  const maxW = { sm: '24rem', md: '32rem', lg: '48rem' };

  function close() {
    if (dismissible) dispatch('close');
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === 'Tab' && dialogEl) {
      const focusables = dialogEl.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
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
      dialogEl?.querySelector<HTMLElement>('input, textarea, select, button')?.focus();
    });
  } else if (lastFocused) {
    lastFocused.focus();
    lastFocused = null;
  }
</script>

<svelte:window on:keydown={open ? onKeydown : undefined} />

{#if open}
  <div class="scrim" transition:fade={{ duration: 120 }} on:click={close} aria-hidden="true"></div>
  <div class="modal-positioner" role="dialog" aria-modal="true" aria-label={title ?? 'Dialog'}>
    <div
      bind:this={dialogEl}
      class="modal"
      style="max-width: {maxW[size]};"
      transition:scale={{ start: 0.96, opacity: 0, duration: 160 }}
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
      <div class="px-5 py-4">
        <slot />
      </div>
      {#if $$slots.footer}
        <footer class="flex justify-end gap-2 border-t border-border px-5 py-3.5">
          <slot name="footer" />
        </footer>
      {/if}
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: rgba(10, 10, 10, 0.32);
    z-index: 40;
  }
  .modal-positioner {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
    pointer-events: none;
  }
  .modal {
    pointer-events: auto;
    width: 100%;
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-2);
    max-height: calc(100vh - 2rem);
    overflow-y: auto;
  }
</style>

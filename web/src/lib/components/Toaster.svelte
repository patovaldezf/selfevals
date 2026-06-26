<script lang="ts">
  import { toast, type Toast } from '$lib/stores/toasts';
  import { fly, fade } from 'svelte/transition';
  import { flip } from 'svelte/animate';

  const glyph: Record<Toast['kind'], string> = {
    success: '✓',
    error: '✕',
    info: 'i'
  };
</script>

<div class="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2" aria-live="polite">
  {#each $toast as t (t.id)}
    <div
      animate:flip={{ duration: 180 }}
      in:fly={{ y: 8, duration: 180 }}
      out:fade={{ duration: 120 }}
      class="toast pointer-events-auto flex max-w-sm items-start gap-2.5 rounded-md border bg-surface px-3.5 py-2.5 shadow-2"
      class:toast-success={t.kind === 'success'}
      class:toast-error={t.kind === 'error'}
      class:toast-info={t.kind === 'info'}
      role="status"
    >
      <span
        class="glyph mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
        >{glyph[t.kind]}</span
      >
      <div class="min-w-0 flex-1">
        <p class="text-sm font-medium text-text-1">{t.message}</p>
        {#if t.description}
          <p class="mt-0.5 break-words text-xs text-text-2">{t.description}</p>
        {/if}
      </div>
      <button
        class="shrink-0 text-text-3 transition-colors hover:text-text-1"
        aria-label="Dismiss"
        on:click={() => toast.dismiss(t.id)}>✕</button
      >
    </div>
  {/each}
</div>

<style>
  .toast {
    border-color: var(--color-border);
  }
  .toast-success {
    border-left: 2px solid var(--color-success);
  }
  .toast-error {
    border-left: 2px solid var(--color-danger);
  }
  .toast-info {
    border-left: 2px solid var(--color-text-3);
  }
  .toast-success .glyph {
    background: var(--color-success);
    color: #fff;
  }
  .toast-error .glyph {
    background: var(--color-danger);
    color: #fff;
  }
  .toast-info .glyph {
    background: var(--color-text-3);
    color: #fff;
  }
</style>

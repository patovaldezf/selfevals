<script lang="ts">
  /** Error state: something failed to load. Surfaces the message and, when a
   *  `retry` handler is given, an explicit way out — an error the user can't
   *  act on is worse than one they can retry. */
  export let title: string = 'Something went wrong';
  export let message: string | null = null;
  export let retry: (() => void) | null = null;
</script>

<div
  class="flex flex-col items-center justify-center rounded-lg border border-border bg-surface px-8 py-12 text-center"
  role="alert"
>
  <div
    class="mb-3 flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold text-accent-fg"
    style="background: var(--color-danger);"
    aria-hidden="true"
  >
    !
  </div>
  <h3 class="text-sm font-semibold text-text-1">{title}</h3>
  {#if message}
    <p class="mt-1.5 max-w-md break-words text-sm text-text-2">{message}</p>
  {/if}
  {#if retry}
    <button
      class="mt-5 rounded-md border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium text-text-1 transition-colors hover:bg-surface-2"
      on:click={retry}
    >
      Try again
    </button>
  {/if}
</div>

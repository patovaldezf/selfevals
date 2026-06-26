<script lang="ts">
  /** A confirm dialog built on Modal. For irreversible actions (cancel a run,
   *  freeze a dataset, retire a failure mode). `tone="danger"` for destructive
   *  confirms. `onConfirm` may be async — the button shows a spinner and the
   *  dialog stays open until it resolves, so a slow mutation can't be lost. */
  import Modal from './Modal.svelte';
  import Button from './Button.svelte';
  import { createEventDispatcher } from 'svelte';

  export let open = false;
  export let title: string;
  export let message: string | null = null;
  export let confirmLabel = 'Confirm';
  export let cancelLabel = 'Cancel';
  export let tone: 'primary' | 'danger' = 'primary';
  export let onConfirm: () => void | Promise<void>;

  const dispatch = createEventDispatcher<{ close: void }>();
  let busy = false;

  async function confirm() {
    busy = true;
    try {
      await onConfirm();
      dispatch('close');
    } finally {
      busy = false;
    }
  }
</script>

<Modal {open} {title} size="sm" dismissible={!busy} on:close={() => dispatch('close')}>
  {#if message}
    <p class="text-sm text-text-2">{message}</p>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" disabled={busy} on:click={() => dispatch('close')}>{cancelLabel}</Button
    >
    <Button variant={tone} loading={busy} on:click={confirm}>{confirmLabel}</Button>
  </svelte:fragment>
</Modal>

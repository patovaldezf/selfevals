<script lang="ts">
  /** Hover/focus tooltip. The default slot is the trigger; `text` is the label.
   *  Shows after a short delay so a sweep across the UI doesn't flash tips, and
   *  fades quickly. CSS-positioned around the trigger — no portal, so keep tips
   *  short. Appears on keyboard focus too, not just mouse. */
  import { fade } from 'svelte/transition';

  export let text: string;
  export let placement: 'top' | 'bottom' | 'left' | 'right' = 'top';
  export let delay = 150;

  let visible = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  function show() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => (visible = true), delay);
  }
  function hide() {
    if (timer) clearTimeout(timer);
    timer = null;
    visible = false;
  }
</script>

<span
  class="tt-wrap"
  on:mouseenter={show}
  on:mouseleave={hide}
  on:focusin={show}
  on:focusout={hide}
  role="presentation"
>
  <slot />
  {#if visible && text}
    <span class="tt tt-{placement}" role="tooltip" transition:fade={{ duration: 100 }}>
      {text}
    </span>
  {/if}
</span>

<style>
  .tt-wrap {
    position: relative;
    display: inline-flex;
  }
  .tt {
    position: absolute;
    z-index: 60;
    padding: 0.25rem 0.5rem;
    font-size: var(--text-2xs);
    font-weight: 500;
    line-height: 1.3;
    white-space: nowrap;
    color: var(--color-bg);
    background: var(--color-text-1);
    border-radius: var(--radius-sm);
    box-shadow: var(--shadow-2);
    pointer-events: none;
  }
  .tt-top {
    bottom: calc(100% + 0.4rem);
    left: 50%;
    transform: translateX(-50%);
  }
  .tt-bottom {
    top: calc(100% + 0.4rem);
    left: 50%;
    transform: translateX(-50%);
  }
  .tt-left {
    right: calc(100% + 0.4rem);
    top: 50%;
    transform: translateY(-50%);
  }
  .tt-right {
    left: calc(100% + 0.4rem);
    top: 50%;
    transform: translateY(-50%);
  }
</style>

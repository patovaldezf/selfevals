<script lang="ts">
  /** Tab bar with an underline that slides between tabs instead of snapping —
   *  the small touch that reads as "designed". Bind `active`; arrow keys move
   *  between tabs (roving focus). Replaces the hand-rolled setTab string checks. */
  import type { ComponentType } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import Icon from './Icon.svelte';

  export let tabs: { id: string; label: string; icon?: ComponentType }[] = [];
  export let active: string = tabs[0]?.id ?? '';

  const dispatch = createEventDispatcher<{ change: string }>();
  let btns: Record<string, HTMLButtonElement> = {};
  let indicator = { left: 0, width: 0 };

  function select(id: string) {
    if (id === active) return;
    active = id;
    dispatch('change', id);
  }

  function onKeydown(e: KeyboardEvent, idx: number) {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next =
      e.key === 'ArrowRight' ? (idx + 1) % tabs.length : (idx - 1 + tabs.length) % tabs.length;
    const id = tabs[next].id;
    select(id);
    btns[id]?.focus();
  }

  // Track the active tab's geometry so the underline can transition to it.
  $: if (active && btns[active]) {
    const el = btns[active];
    indicator = { left: el.offsetLeft, width: el.offsetWidth };
  }
</script>

<div class="tabs" role="tablist">
  {#each tabs as tab, idx (tab.id)}
    <button
      bind:this={btns[tab.id]}
      class="tab"
      class:active={active === tab.id}
      role="tab"
      aria-selected={active === tab.id}
      tabindex={active === tab.id ? 0 : -1}
      on:click={() => select(tab.id)}
      on:keydown={(e) => onKeydown(e, idx)}
    >
      {#if tab.icon}
        <Icon icon={tab.icon} size={14} />
      {/if}
      {tab.label}
    </button>
  {/each}
  <span class="indicator" style="left: {indicator.left}px; width: {indicator.width}px;"></span>
</div>

<style>
  .tabs {
    position: relative;
    display: flex;
    align-items: center;
    gap: 0.25rem;
    border-bottom: 1px solid var(--color-border);
  }
  .tab {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.5rem 0.65rem;
    font-size: var(--text-sm);
    font-weight: 500;
    color: var(--color-text-2);
    background: transparent;
    border: none;
    transition: color var(--dur-base) var(--ease-out);
  }
  .tab:hover {
    color: var(--color-text-1);
  }
  .tab.active {
    color: var(--color-text-1);
  }
  .tab:focus-visible {
    outline: 2px solid var(--color-brand);
    outline-offset: -2px;
    border-radius: var(--radius-sm);
  }
  .indicator {
    position: absolute;
    bottom: -1px;
    height: 2px;
    background: var(--color-text-1);
    border-radius: 1px;
    transition:
      left var(--dur-base) var(--ease-out),
      width var(--dur-base) var(--ease-out);
  }
  @media (prefers-reduced-motion: reduce) {
    .indicator {
      transition: none;
    }
  }
</style>

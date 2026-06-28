<script lang="ts">
  /** The one badge. Centralises every status pill so the tone→colour mapping
   *  lives in one place instead of ad-hoc `tone-candidate` classes scattered
   *  across pages. Background is the low-alpha `-subtle` token, text the solid
   *  tone — readable and quiet at the same time. `icon` reinforces meaning. */
  import type { ComponentType } from 'svelte';
  import Icon from './Icon.svelte';

  export let tone:
    | 'neutral'
    | 'brand'
    | 'ok'
    | 'warn'
    | 'bad'
    | 'candidate'
    | 'official'
    | 'retired' = 'neutral';
  export let size: 'sm' | 'md' = 'md';
  export let icon: ComponentType | null = null;
</script>

<span class="badge badge-{tone} badge-{size}">
  {#if icon}
    <Icon {icon} size={size === 'sm' ? 12 : 13} strokeWidth={2} />
  {/if}
  <slot />
</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-weight: 500;
    border-radius: var(--radius-sm);
    white-space: nowrap;
    line-height: 1.2;
  }
  .badge-sm {
    font-size: var(--text-2xs);
    padding: 0.1rem 0.35rem;
  }
  .badge-md {
    font-size: var(--text-xs);
    padding: 0.15rem 0.45rem;
  }

  /* candidate maps to warn, official to ok, retired to neutral — the taxonomy
     lifecycle reads as "in review / accepted / archived" through colour. */
  .badge-neutral,
  .badge-retired {
    background: var(--color-surface-2);
    color: var(--color-text-2);
  }
  .badge-brand {
    background: var(--color-brand-subtle);
    color: var(--color-brand-strong);
  }
  .badge-ok,
  .badge-official {
    background: var(--color-ok-subtle);
    color: var(--color-ok);
  }
  .badge-warn,
  .badge-candidate {
    background: var(--color-warn-subtle);
    color: var(--color-warn);
  }
  .badge-bad {
    background: var(--color-bad-subtle);
    color: var(--color-bad);
  }
</style>

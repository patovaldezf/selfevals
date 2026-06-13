<script lang="ts">
  /** The one button. Variants map to intent (primary action / secondary /
   *  quiet / destructive); `loading` disables and shows a spinner so a slow
   *  mutation can't be double-fired. */
  export let variant: 'primary' | 'secondary' | 'ghost' | 'danger' = 'secondary';
  export let size: 'sm' | 'md' = 'md';
  export let type: 'button' | 'submit' | 'reset' = 'button';
  export let disabled = false;
  export let loading = false;
  export let href: string | null = null;
  export let title: string | null = null;

  $: isDisabled = disabled || loading;
</script>

<svelte:element
  this={href ? 'a' : 'button'}
  {href}
  {title}
  type={href ? undefined : type}
  role={href ? 'button' : undefined}
  aria-disabled={href && isDisabled ? 'true' : undefined}
  aria-busy={loading}
  disabled={href ? undefined : isDisabled}
  class="btn btn-{variant} btn-{size}"
  class:btn-disabled={isDisabled}
  on:click
>
  {#if loading}
    <span class="spinner" aria-hidden="true"></span>
  {/if}
  <slot />
</svelte:element>

<style>
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    font-weight: 500;
    border-radius: var(--radius-md);
    border: 1px solid transparent;
    transition:
      background-color 0.14s ease-out,
      border-color 0.14s ease-out,
      opacity 0.14s ease-out,
      transform 0.06s ease-out;
    white-space: nowrap;
    text-decoration: none;
  }
  .btn:active:not(.btn-disabled) {
    transform: translateY(0.5px);
  }
  .btn:focus-visible {
    outline: 2px solid var(--color-accent);
    outline-offset: 2px;
  }
  .btn-sm {
    font-size: 13px;
    padding: 0.3rem 0.65rem;
  }
  .btn-md {
    font-size: 14px;
    padding: 0.45rem 0.85rem;
  }
  .btn-primary {
    background: var(--color-accent);
    color: var(--color-accent-fg);
  }
  .btn-primary:hover:not(.btn-disabled) {
    background: #000;
  }
  .btn-secondary {
    background: var(--color-surface);
    border-color: var(--color-border-strong);
    color: var(--color-text-1);
  }
  .btn-secondary:hover:not(.btn-disabled) {
    background: var(--color-surface-2);
  }
  .btn-ghost {
    background: transparent;
    color: var(--color-text-2);
  }
  .btn-ghost:hover:not(.btn-disabled) {
    background: var(--color-surface-2);
    color: var(--color-text-1);
  }
  .btn-danger {
    background: var(--color-danger);
    color: #fff;
  }
  .btn-danger:hover:not(.btn-disabled) {
    filter: brightness(0.92);
  }
  .btn-disabled {
    opacity: 0.5;
    cursor: not-allowed;
    pointer-events: none;
  }
  .spinner {
    width: 0.85em;
    height: 0.85em;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation-duration: 1.2s;
    }
  }
</style>

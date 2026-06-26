<script lang="ts">
  /** Empty state: when a list has no items yet. An empty state should invite
   *  the next action, not just report absence — pass a CTA via the `action`
   *  slot. The default slot is optional supporting copy. */
  export let title: string;
  export let description: string | null = null;
  /** Optional glyph/emoji shown above the title. Keep it quiet. */
  export let icon: string | null = null;
</script>

<div
  class="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-surface px-8 py-16 text-center"
>
  {#if icon}
    <div class="mb-3 text-2xl text-text-3" aria-hidden="true">{icon}</div>
  {/if}
  <h3 class="text-sm font-semibold text-text-1">{title}</h3>
  {#if description}
    <p class="mt-1.5 max-w-sm text-sm text-text-2">{description}</p>
  {/if}
  <slot />
  {#if $$slots.action}
    <div class="mt-5">
      <slot name="action" />
    </div>
  {/if}
</div>

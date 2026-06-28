<script lang="ts">
  /** Thin wrapper over lucide-svelte so the rest of the app imports icons from
   *  one place with consistent sizing and stroke. Pass a lucide icon component
   *  via `icon`. Default 16px, 1.75 stroke — reads crisp at UI density and
   *  matches the Linear-ish weight. Icons reinforce text, never replace it, so
   *  they default to aria-hidden; pass `label` for a standalone meaningful icon. */
  import type { ComponentType } from 'svelte';

  export let icon: ComponentType;
  export let size: number = 16;
  export let strokeWidth: number = 1.75;
  export let label: string | null = null;
  let cls = '';
  export { cls as class };
</script>

<svelte:component
  this={icon}
  {size}
  {strokeWidth}
  class={cls}
  aria-hidden={label ? undefined : true}
  aria-label={label ?? undefined}
  role={label ? 'img' : undefined}
/>

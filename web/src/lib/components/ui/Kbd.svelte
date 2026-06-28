<script lang="ts">
  /** Renders a keyboard shortcut as a row of keycaps. Keep keys short
   *  (['⌘','K'], ['g','e']) — they're hints next to actions, not prose. */
  export let keys: string[] = [];
</script>

<span class="kbd-group">
  {#each keys as key}
    <kbd class="kbd">{key}</kbd>
  {/each}
</span>

<style>
  .kbd-group {
    display: inline-flex;
    align-items: center;
    gap: 0.15rem;
  }
  .kbd {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.25rem;
    height: 1.25rem;
    padding: 0 0.3rem;
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    line-height: 1;
    color: var(--color-text-2);
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    border-bottom-width: 2px;
    border-radius: var(--radius-sm);
  }
</style>

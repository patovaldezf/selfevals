<script lang="ts">
  export let outcome: string | null;

  const palette: Record<string, { fg: string; bg: string; label: string }> = {
    keep_candidate: { fg: '#0F7B3E', bg: '#E8F5EE', label: 'keep' },
    reject: { fg: '#525252', bg: '#F0F0EE', label: 'reject' },
    revert: { fg: '#B91C1C', bg: '#FBE9E9', label: 'revert' },
    feature_flag: { fg: '#1F1F1F', bg: '#EFEFED', label: 'flag' },
    investigate: { fg: '#B45309', bg: '#FBEFD9', label: 'investigate' },
    require_tradeoff_review: { fg: '#B45309', bg: '#FBEFD9', label: 'tradeoff' },
    spawn_subexperiment: { fg: '#1F1F1F', bg: '#EFEFED', label: 'subexp' }
  };

  $: meta = outcome ? palette[outcome] : null;
</script>

{#if meta}
  <span
    class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
    style="color: {meta.fg}; background: {meta.bg};"
  >
    {meta.label}
  </span>
{:else}
  <span class="text-text-3 text-xs">—</span>
{/if}

<script lang="ts">
  import type { LayoutData } from '../$types';
  export let data: LayoutData;
</script>

<svelte:head>
  <title>Datasets · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-5xl mx-auto">
  <header class="mb-8">
    <h1 class="text-2xl font-semibold tracking-tight">Datasets</h1>
    <p class="text-text-2 mt-1.5 text-sm">
      Cases grouped by taxonomy: level, feature, source, ground truth.
    </p>
  </header>

  <div class="rounded-lg border border-dashed border-border bg-surface px-8 py-16 text-center text-text-3 text-sm">
    Dataset browser ships next. Use the CLI to inspect:<br />
    <code class="font-mono text-xs mt-3 inline-block px-2 py-1 rounded bg-surface-2"
      >cat evals/datasets/*.jsonl | head</code
    >
  </div>
</div>

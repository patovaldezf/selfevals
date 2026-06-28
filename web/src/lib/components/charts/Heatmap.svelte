<script lang="ts">
  /**
   * Confusion matrix / NxN heatmap. Cell colour scales with intensity (a tint of
   * the brand accent), with the diagonal — correct predictions — leaning on the
   * "ok" tone so a strong diagonal reads as a healthy classifier at a glance.
   * Hover lifts the raw value with its row/col labels. Built for per-class
   * confusion where rows are actual and columns are predicted.
   */
  import { onMount } from 'svelte';

  /** Square (or rectangular) matrix of counts: matrix[row][col]. */
  export let matrix: number[][] = [];
  export let rowLabels: string[] = [];
  export let colLabels: string[] = [];
  /** Treat the diagonal as "correct" and tint it green. */
  export let diagonalIsCorrect = true;
  export let cellSize = 40;

  $: maxVal = Math.max(1, ...matrix.flat().filter((v) => Number.isFinite(v)));

  let hover: { r: number; c: number } | null = null;

  function cellBg(r: number, c: number, v: number): string {
    const t = Math.min(1, v / maxVal);
    if (t === 0) return 'var(--color-surface-2)';
    // Diagonal hits read as ok/green; off-diagonal (errors) as the neutral
    // brand tint scaling with how many landed there.
    const tone = diagonalIsCorrect && r === c ? '15, 157, 88' : '91, 91, 214';
    return `rgba(${tone}, ${(0.12 + t * 0.78).toFixed(3)})`;
  }
  function cellFg(v: number): string {
    return v / maxVal > 0.55 ? '#fff' : 'var(--color-text-1)';
  }

  let mounted = false;
  onMount(() => {
    mounted = true;
  });
</script>

{#if matrix.length === 0}
  <p class="py-4 text-center text-sm text-text-3">No confusion data.</p>
{:else}
  <div class="inline-block overflow-x-auto">
    <table class="border-collapse" style="font-variant-numeric: tabular-nums;">
      <thead>
        <tr>
          <th class="p-1"></th>
          {#each colLabels as cl}
            <th
              class="px-1 pb-1 text-[10px] font-medium text-text-3"
              style="max-width: {cellSize}px;"
            >
              <span class="block truncate" title={cl}>{cl}</span>
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each matrix as row, r}
          <tr>
            <th
              class="pr-2 text-right text-[10px] font-medium text-text-3"
              style="max-width: 90px;"
            >
              <span class="block truncate" title={rowLabels[r] ?? `${r}`}>{rowLabels[r] ?? r}</span>
            </th>
            {#each row as v, c}
              <td class="p-0.5">
                <div
                  class="flex items-center justify-center rounded-sm font-mono text-xs"
                  style="width: {cellSize}px; height: {cellSize}px; background: {mounted
                    ? cellBg(r, c, v)
                    : 'var(--color-surface-2)'}; color: {cellFg(
                    v
                  )}; transition: background 0.4s var(--ease-out); outline: {hover &&
                  hover.r === r &&
                  hover.c === c
                    ? '2px solid var(--color-brand)'
                    : 'none'};"
                  role="img"
                  aria-label="{rowLabels[r] ?? r} predicted {colLabels[c] ?? c}: {v}"
                  on:mouseenter={() => (hover = { r, c })}
                  on:mouseleave={() => (hover = null)}
                  data-numeric
                >
                  {v}
                </div>
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}

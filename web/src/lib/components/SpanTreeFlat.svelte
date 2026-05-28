<script lang="ts">
  /**
   * Flattened, windowed span tree (A8).
   *
   * The recursive `SpanNode` is fine when a trace has tens of spans. At
   * 1000+ spans the DOM render and re-render on every selection change
   * starts to drag. This component flattens the tree depth-first, tracks
   * collapsed subtrees, and renders only the slice of rows currently
   * inside the viewport (plus a buffer) — so a 10k-span trace mounts as
   * if it were 30 rows.
   *
   * Row height is approximated (single line, fixed font). The window
   * arithmetic uses that approximation; a few extra pixels of overscan
   * absorb the rounding error.
   *
   * Collapse state lives here, not in the per-row component, because the
   * flatten must reflect collapse decisions to honor scroll math (a
   * collapsed subtree is zero rows tall).
   */
  import type { SpanSummary } from '$lib/api/client';
  import { factsFor } from '$lib/spans/facts';
  import { styleForKind } from '$lib/spans/kindStyle';

  export let tree: Map<string | null, SpanSummary[]>;
  export let selected: SpanSummary | null;
  export let setSelected: (s: SpanSummary) => void;

  const ROW_HEIGHT_PX = 28;
  /** How many extra rows to render above/below the viewport so fast
   *  scrolls stay smooth. Two-screen overscan is plenty for arrow keys
   *  and trackpad fling. */
  const OVERSCAN_ROWS = 12;

  type Row = { node: SpanSummary; depth: number; hasChildren: boolean };

  let collapsed: Set<string> = new Set();

  function flatten(
    byParent: Map<string | null, SpanSummary[]>,
    collapsedIds: Set<string>
  ): Row[] {
    const out: Row[] = [];
    const walk = (parentId: string | null, depth: number): void => {
      const children = byParent.get(parentId) ?? [];
      for (const node of children) {
        const grandkids = byParent.get(node.id) ?? [];
        out.push({ node, depth, hasChildren: grandkids.length > 0 });
        if (grandkids.length > 0 && !collapsedIds.has(node.id)) {
          walk(node.id, depth + 1);
        }
      }
    };
    walk(null, 0);
    return out;
  }

  $: rows = flatten(tree, collapsed);

  // Track scroll position + container height to compute the visible window.
  let scrollTop = 0;
  let viewportHeight = 0;

  function onScroll(e: Event) {
    scrollTop = (e.target as HTMLElement).scrollTop;
  }

  $: visibleStart = Math.max(
    0,
    Math.floor(scrollTop / ROW_HEIGHT_PX) - OVERSCAN_ROWS
  );
  $: visibleCount =
    Math.ceil(viewportHeight / ROW_HEIGHT_PX) + OVERSCAN_ROWS * 2;
  $: visibleEnd = Math.min(rows.length, visibleStart + visibleCount);
  $: visibleRows = rows.slice(visibleStart, visibleEnd);
  $: padTop = visibleStart * ROW_HEIGHT_PX;
  $: padBottom = (rows.length - visibleEnd) * ROW_HEIGHT_PX;

  function toggle(id: string) {
    const next = new Set(collapsed);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    collapsed = next;
  }

  function fmtDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }
</script>

<div
  class="overflow-y-auto"
  style:max-height="60vh"
  bind:clientHeight={viewportHeight}
  on:scroll={onScroll}
>
  {#if padTop > 0}
    <div aria-hidden="true" style:height="{padTop}px"></div>
  {/if}
  <ul class="text-sm">
    {#each visibleRows as row (row.node.id)}
      {@const style = styleForKind(row.node.kind)}
      {@const facts = factsFor(row.node)}
      {@const isCollapsed = collapsed.has(row.node.id)}
      <li style:height="{ROW_HEIGHT_PX}px" class="flex">
        <button
          type="button"
          class="w-full flex items-center gap-2 rounded px-2 py-1 text-left hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-text-1 transition-colors
                 {selected?.id === row.node.id ? 'bg-surface-2' : ''}"
          style:padding-left="{row.depth * 14 + 8}px"
          on:click={() => setSelected(row.node)}
          aria-label="{style.label} span: {row.node.name}"
        >
          {#if row.hasChildren}
            <span
              role="button"
              tabindex="0"
              class="text-text-3 hover:text-text-1 text-[10px] w-3 text-center shrink-0 cursor-pointer"
              on:click|stopPropagation={() => toggle(row.node.id)}
              on:keydown|stopPropagation={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggle(row.node.id);
                }
              }}
              aria-label={isCollapsed ? 'Expand subtree' : 'Collapse subtree'}
            >{isCollapsed ? '▸' : '▾'}</span>
          {:else}
            <span class="w-3 shrink-0" aria-hidden="true"></span>
          {/if}
          <span
            class="inline-block text-[12px] leading-none flex-shrink-0 w-3 text-center"
            style:color={style.color}
            aria-hidden="true"
            title={style.label}
          >{style.glyph}</span>
          <span class="font-mono text-xs truncate flex-1 min-w-0">{row.node.name}</span>
          {#each facts as f (f.key)}
            <span
              class="font-mono text-[10px] text-text-3 hidden sm:inline-block whitespace-nowrap"
              title={f.title ?? f.key}
              data-numeric
            >
              {f.value}
            </span>
          {/each}
          <span
            class="font-mono text-[10px] text-text-3 whitespace-nowrap"
            title="duration"
            data-numeric
          >{fmtDuration(row.node.duration_ms)}</span>
        </button>
      </li>
    {/each}
  </ul>
  {#if padBottom > 0}
    <div aria-hidden="true" style:height="{padBottom}px"></div>
  {/if}
</div>

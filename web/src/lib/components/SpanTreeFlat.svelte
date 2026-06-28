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
  import Icon from '$lib/components/ui/Icon.svelte';
  import { ChevronRight } from 'lucide-svelte';

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
  let viewportEl: HTMLElement | null = null;

  function flatten(byParent: Map<string | null, SpanSummary[]>, collapsedIds: Set<string>): Row[] {
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

  // Longest span drives the duration mini-bar scale — a relative read of
  // "where did the time go" without leaving the tree.
  $: maxDuration = rows.reduce((m, r) => Math.max(m, r.node.duration_ms), 1);

  // Track scroll position + container height to compute the visible window.
  let scrollTop = 0;
  let viewportHeight = 0;

  function onScroll(e: Event) {
    scrollTop = (e.target as HTMLElement).scrollTop;
  }

  $: visibleStart = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT_PX) - OVERSCAN_ROWS);
  $: visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT_PX) + OVERSCAN_ROWS * 2;
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

  function isError(node: SpanSummary): boolean {
    if (node.kind === 'error') return true;
    const fs = node.detail.final_state as { status?: string } | undefined;
    const status = (node.detail.status as string | undefined) ?? fs?.status;
    return status === 'error' || status === 'failed' || node.detail.error != null;
  }

  // Scroll a row into view by its index in the flattened list.
  function scrollRowIntoView(index: number) {
    if (!viewportEl) return;
    const top = index * ROW_HEIGHT_PX;
    const bottom = top + ROW_HEIGHT_PX;
    if (top < viewportEl.scrollTop) viewportEl.scrollTop = top;
    else if (bottom > viewportEl.scrollTop + viewportHeight)
      viewportEl.scrollTop = bottom - viewportHeight;
  }

  // Keyboard navigation: j/k or arrows move the selection through the
  // flattened list (collapsed subtrees are already excluded), Enter/Space
  // re-selects, left/right collapse/expand. The whole tree is one focusable
  // surface so arrow keys feel like a real list, not tab-stop hopping.
  function onKeydown(e: KeyboardEvent) {
    const idx = selected ? rows.findIndex((r) => r.node.id === selected!.id) : -1;
    if (e.key === 'ArrowDown' || e.key === 'j') {
      e.preventDefault();
      const next = Math.min(rows.length - 1, idx + 1);
      if (rows[next]) {
        setSelected(rows[next].node);
        scrollRowIntoView(next);
      }
    } else if (e.key === 'ArrowUp' || e.key === 'k') {
      e.preventDefault();
      const prev = idx <= 0 ? 0 : idx - 1;
      if (rows[prev]) {
        setSelected(rows[prev].node);
        scrollRowIntoView(prev);
      }
    } else if (e.key === 'ArrowRight') {
      if (idx >= 0 && rows[idx].hasChildren && collapsed.has(rows[idx].node.id)) {
        e.preventDefault();
        toggle(rows[idx].node.id);
      }
    } else if (e.key === 'ArrowLeft') {
      if (idx >= 0 && rows[idx].hasChildren && !collapsed.has(rows[idx].node.id)) {
        e.preventDefault();
        toggle(rows[idx].node.id);
      }
    }
  }
</script>

<div
  class="tree"
  bind:this={viewportEl}
  bind:clientHeight={viewportHeight}
  on:scroll={onScroll}
  on:keydown={onKeydown}
  tabindex="0"
  role="tree"
  aria-label="Span tree"
>
  {#if padTop > 0}
    <div aria-hidden="true" style:height="{padTop}px"></div>
  {/if}
  <ul>
    {#each visibleRows as row (row.node.id)}
      {@const style = styleForKind(row.node.kind)}
      {@const facts = factsFor(row.node)}
      {@const isCollapsed = collapsed.has(row.node.id)}
      {@const err = isError(row.node)}
      {@const isSel = selected?.id === row.node.id}
      <li style:height="{ROW_HEIGHT_PX}px" class="row-li">
        <button
          type="button"
          class="row"
          class:row-sel={isSel}
          class:row-err={err}
          style:padding-left="{row.depth * 14 + 8}px"
          on:click={() => setSelected(row.node)}
          aria-label="{style.label} span: {row.node.name}"
          aria-selected={isSel}
          role="treeitem"
        >
          {#if row.hasChildren}
            <span
              role="button"
              tabindex="-1"
              class="caret"
              class:caret-open={!isCollapsed}
              on:click|stopPropagation={() => toggle(row.node.id)}
              on:keydown|stopPropagation={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggle(row.node.id);
                }
              }}
              aria-label={isCollapsed ? 'Expand subtree' : 'Collapse subtree'}
            >
              <Icon icon={ChevronRight} size={12} />
            </span>
          {:else}
            <span class="caret-spacer" aria-hidden="true"></span>
          {/if}

          <span class="kind-icon" style:color={style.color} aria-hidden="true" title={style.label}>
            <Icon icon={style.icon} size={13} strokeWidth={2} />
          </span>

          <span class="row-name" class:row-name-err={err}>{row.node.name}</span>

          {#each facts as f (f.key)}
            <span class="fact" title={f.title ?? f.key} data-numeric>{f.value}</span>
          {/each}

          <!-- Duration as a proportional bar + label: a glance read of where the
               time went, scaled to the slowest span in the trace. -->
          <span class="dur" data-numeric title="duration">
            <span class="dur-bar" aria-hidden="true">
              <span
                class="dur-fill"
                class:dur-fill-err={err}
                style:width="{Math.max(2, (row.node.duration_ms / maxDuration) * 100)}%"
              ></span>
            </span>
            {fmtDuration(row.node.duration_ms)}
          </span>
        </button>
      </li>
    {/each}
  </ul>
  {#if padBottom > 0}
    <div aria-hidden="true" style:height="{padBottom}px"></div>
  {/if}
</div>

<style>
  .tree {
    max-height: 60vh;
    overflow-y: auto;
    border-radius: var(--radius-md);
    outline: none;
  }
  .tree:focus-visible {
    box-shadow: 0 0 0 2px var(--color-brand-subtle);
  }
  ul {
    font-size: var(--text-sm);
  }
  .row-li {
    display: flex;
  }
  .row {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 0.45rem;
    border-radius: var(--radius-sm);
    padding-top: 0.25rem;
    padding-bottom: 0.25rem;
    padding-right: 0.5rem;
    text-align: left;
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  .row:hover {
    background: var(--color-surface-2);
  }
  .row-sel {
    background: var(--color-surface-2);
    box-shadow: inset 2px 0 0 var(--color-brand);
  }
  /* A failed span reads red from the tree without opening it. */
  .row-err {
    background: var(--color-bad-subtle);
  }
  .row-err.row-sel {
    box-shadow: inset 2px 0 0 var(--color-bad);
  }
  .caret {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 0.85rem;
    flex-shrink: 0;
    color: var(--color-text-3);
    cursor: pointer;
    transition:
      transform var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .caret:hover {
    color: var(--color-text-1);
  }
  .caret-open {
    transform: rotate(90deg);
  }
  .caret-spacer {
    width: 0.85rem;
    flex-shrink: 0;
  }
  .kind-icon {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
  }
  .row-name {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--color-text-1);
  }
  .row-name-err {
    color: var(--color-bad);
  }
  .fact {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .dur {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .dur-bar {
    width: 36px;
    height: 4px;
    border-radius: 2px;
    background: var(--color-surface-3);
    overflow: hidden;
  }
  .dur-fill {
    display: block;
    height: 100%;
    border-radius: 2px;
    background: var(--color-chart-2);
  }
  .dur-fill-err {
    background: var(--color-bad);
  }
</style>

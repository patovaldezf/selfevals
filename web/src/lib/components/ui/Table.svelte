<script lang="ts" context="module">
  // Exported types must live in the module script so callers can import them.
  type Align = 'left' | 'right' | 'center';
  export type Column = {
    key: string;
    label: string;
    align?: Align;
    sortable?: boolean;
    numeric?: boolean;
    width?: string;
  };
  export type Row = Record<string, unknown>;
</script>

<script lang="ts">
  /** Sortable, keyboard-navigable table at Linear density. Pass `columns` and
   *  `rows`; clicking a sortable header cycles asc → desc → none. The default
   *  slot renders a cell, receiving `row`, `column`, and `value`, so callers can
   *  drop in badges, links, or formatted numbers. Numeric columns get
   *  tabular-nums + right alignment for free. Rows emit `rowClick`. */
  import { createEventDispatcher } from 'svelte';
  import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-svelte';
  import Icon from './Icon.svelte';

  export let columns: Column[] = [];
  export let rows: Row[] = [];
  export let sortKey: string | null = null;
  export let sortDir: 'asc' | 'desc' | null = null;
  /** When true the component sorts `rows` in place; set false to sort server-side
   *  and just listen to the `sort` event. */
  export let clientSort = true;
  /** Rows are interactive (pointer cursor + Enter to activate) by default. Set
   *  false for a read-only table with no rowClick handler. */
  export let clickable = true;

  const dispatch = createEventDispatcher<{
    rowClick: Row;
    sort: { key: string; dir: 'asc' | 'desc' | null };
  }>();

  function cycle(col: Column) {
    if (!col.sortable) return;
    let dir: 'asc' | 'desc' | null;
    if (sortKey !== col.key) dir = 'asc';
    else if (sortDir === 'asc') dir = 'desc';
    else if (sortDir === 'desc') dir = null;
    else dir = 'asc';
    sortKey = dir ? col.key : null;
    sortDir = dir;
    dispatch('sort', { key: col.key, dir });
  }

  function cmp(a: unknown, b: unknown): number {
    if (a === b) return 0;
    if (a === null || a === undefined) return -1;
    if (b === null || b === undefined) return 1;
    if (typeof a === 'number' && typeof b === 'number') return a - b;
    return String(a).localeCompare(String(b));
  }

  $: sorted =
    clientSort && sortKey && sortDir
      ? [...rows].sort((r1, r2) => {
          const d = cmp(r1[sortKey as string], r2[sortKey as string]);
          return sortDir === 'asc' ? d : -d;
        })
      : rows;
</script>

<div class="table-wrap">
  <table>
    <thead>
      <tr>
        {#each columns as col (col.key)}
          <th
            class:numeric={col.numeric}
            class:sortable={col.sortable}
            style:text-align={col.align ?? (col.numeric ? 'right' : 'left')}
            style:width={col.width}
            aria-sort={sortKey === col.key
              ? sortDir === 'asc'
                ? 'ascending'
                : sortDir === 'desc'
                  ? 'descending'
                  : 'none'
              : undefined}
          >
            {#if col.sortable}
              <button class="th-btn" on:click={() => cycle(col)}>
                <span>{col.label}</span>
                <span class="sort-icon" class:dim={sortKey !== col.key}>
                  {#if sortKey === col.key && sortDir === 'asc'}
                    <Icon icon={ChevronUp} size={13} strokeWidth={2} />
                  {:else if sortKey === col.key && sortDir === 'desc'}
                    <Icon icon={ChevronDown} size={13} strokeWidth={2} />
                  {:else}
                    <Icon icon={ChevronsUpDown} size={13} strokeWidth={2} />
                  {/if}
                </span>
              </button>
            {:else}
              {col.label}
            {/if}
          </th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each sorted as row, i (i)}
        <tr
          class:clickable
          on:click={() => dispatch('rowClick', row)}
          on:keydown={(e) => (e.key === 'Enter' ? dispatch('rowClick', row) : null)}
          tabindex={clickable ? 0 : undefined}
        >
          {#each columns as col (col.key)}
            <td
              class:numeric={col.numeric}
              style:text-align={col.align ?? (col.numeric ? 'right' : 'left')}
            >
              <slot {row} {col} value={row[col.key]}>{row[col.key] ?? '—'}</slot>
            </td>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .table-wrap {
    width: 100%;
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  thead th {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--color-surface-2);
    color: var(--color-text-3);
    font-size: var(--text-2xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.5rem 0.7rem;
    border-bottom: 1px solid var(--color-border);
    white-space: nowrap;
  }
  th.sortable {
    padding: 0;
  }
  .th-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    width: 100%;
    padding: 0.5rem 0.7rem;
    font: inherit;
    color: inherit;
    text-transform: inherit;
    letter-spacing: inherit;
    background: transparent;
    border: none;
    transition: color var(--dur-fast) var(--ease-out);
  }
  .th-btn:hover {
    color: var(--color-text-1);
  }
  .sort-icon {
    display: inline-flex;
    color: var(--color-text-2);
  }
  .sort-icon.dim {
    opacity: 0;
    transition: opacity var(--dur-fast) var(--ease-out);
  }
  .th-btn:hover .sort-icon.dim {
    opacity: 0.5;
  }
  tbody td {
    padding: 0.55rem 0.7rem;
    border-bottom: 1px solid var(--color-border);
    color: var(--color-text-1);
    vertical-align: middle;
  }
  .numeric {
    font-variant-numeric: tabular-nums;
  }
  tbody tr {
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  tbody tr.clickable {
    cursor: pointer;
  }
  tbody tr:hover {
    background: var(--color-surface-2);
  }
  tbody tr:focus-visible {
    outline: 2px solid var(--color-brand);
    outline-offset: -2px;
  }
</style>

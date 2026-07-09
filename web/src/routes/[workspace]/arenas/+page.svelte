<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../$types';
  import { goto } from '$app/navigation';
  import Button from '$lib/components/ui/Button.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { Swords, ArrowRight } from 'lucide-svelte';

  export let data: PageData & LayoutData;
</script>

<svelte:head>
  <title>Arenas · {data.workspace.name}</title>
</svelte:head>

<div class="page">
  <header class="head">
    <div>
      <h1>Arenas</h1>
      <p class="sub">Code-variant bake-offs — N git branches of one agent, run in parallel.</p>
    </div>
    <div class="head-right">
      <span class="count mono" data-numeric>{data.arenas.length} total</span>
      <Button variant="brand" on:click={() => goto(`/${data.workspace.id}/arenas/new`)}
        >New arena</Button
      >
    </div>
  </header>

  {#if data.arenas.length === 0}
    <div class="empty">
      <Icon icon={Swords} size={22} />
      <p class="empty-title">No arenas yet</p>
      <p class="empty-sub">
        Compare code variants empirically: point an arena at a git repo, register a branch per
        variant (a different prompt, a different provider, a different tool set), and let them run
        in parallel on the same dataset.
      </p>
      <Button variant="brand" on:click={() => goto(`/${data.workspace.id}/arenas/new`)}
        >New arena</Button
      >
    </div>
  {:else}
    <div class="card table-wrap">
      <table>
        <thead>
          <tr>
            <th class="l">Arena</th>
            <th class="l">State</th>
            <th class="r">Round</th>
            <th class="l">Winner</th>
            <th class="r"></th>
          </tr>
        </thead>
        <tbody>
          {#each data.arenas as arena (arena.id)}
            <tr on:click={() => goto(`/${data.workspace.id}/arenas/${arena.id}`)}>
              <td>
                <span class="arena-name">{arena.name}</span>
                <span class="arena-goal">{arena.goal}</span>
              </td>
              <td>
                <span class="state">
                  <StatusDot state={arena.state} />
                  <span class="state-label">{arena.state}</span>
                </span>
              </td>
              <td class="r mono" data-numeric>{arena.current_round}</td>
              <td class="mono dim sm">{arena.winner_variant_id ?? '—'}</td>
              <td class="r"><Icon icon={ArrowRight} size={15} class="row-arrow" /></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 72rem;
    margin: 0 auto;
  }
  .head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.5rem;
  }
  .head-right {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  h1 {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .sub {
    color: var(--color-text-2);
    margin-top: 0.4rem;
    font-size: var(--text-sm);
  }
  .count {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .card {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
  }
  .table-wrap {
    overflow: hidden;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  thead {
    background: var(--color-surface-2);
  }
  th {
    font-weight: 500;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    padding: 0.6rem 0.9rem;
  }
  th.l {
    text-align: left;
  }
  th.r {
    text-align: right;
  }
  tbody tr {
    border-top: 1px solid var(--color-border);
    cursor: pointer;
    transition: background-color var(--dur-fast) var(--ease-out);
  }
  tbody tr:hover {
    background: var(--color-surface-2);
  }
  tbody tr:hover :global(.row-arrow) {
    transform: translateX(2px);
    color: var(--color-text-1);
  }
  td {
    padding: 0.75rem 0.9rem;
    vertical-align: middle;
  }
  td.r {
    text-align: right;
  }
  td.mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: var(--text-xs);
  }
  .dim {
    color: var(--color-text-3);
  }
  td.sm {
    font-size: var(--text-xs);
  }
  .arena-name {
    display: block;
    font-weight: 500;
    color: var(--color-text-1);
  }
  .arena-goal {
    display: block;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    max-width: 30rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 0.15rem;
  }
  .state {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
  }
  .state-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  :global(.row-arrow) {
    color: var(--color-text-3);
    transition:
      transform var(--dur-fast) var(--ease-out),
      color var(--dur-fast) var(--ease-out);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.6rem;
    padding: 3.5rem 1.5rem;
    text-align: center;
    color: var(--color-text-3);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
  }
  .empty-title {
    font-weight: 600;
    color: var(--color-text-1);
  }
  .empty-sub {
    font-size: var(--text-sm);
    color: var(--color-text-2);
    max-width: 32rem;
    line-height: var(--leading-snug);
  }
</style>

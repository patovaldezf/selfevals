<!--
  ResultsTab: per-case expected vs detected vs matched grid for an experiment.

  Lazy like FunnelTab/CompareTab — the per-scenario grid can be large, so it
  stays off the server load and fetches only when the tab opens.
  `includeTurns` re-fetches with per-turn breakdowns for conversation cases;
  a token guard keeps an out-of-order response from clobbering a newer one.
-->
<script lang="ts">
  import CaseResultRow from '$lib/components/CaseResultRow.svelte';
  import { api, ApiError, type ExperimentResults } from '$lib/api/client';

  export let workspaceId: string;
  export let experimentId: string;

  let resultsData: ExperimentResults | null = null;
  let resultsError: string | null = null;
  let resultsLoading = false;
  let resultsIncludeTurns = false;
  let resultsRequest = 0;

  void loadResults(resultsIncludeTurns);

  async function loadResults(includeTurns: boolean): Promise<void> {
    const token = ++resultsRequest;
    resultsLoading = true;
    resultsError = null;
    try {
      const detail = await api.experimentResults(workspaceId, experimentId, { includeTurns });
      if (token !== resultsRequest) return;
      resultsData = detail;
    } catch (err) {
      if (token !== resultsRequest) return;
      resultsData = null;
      resultsError =
        err instanceof ApiError && err.status === 404
          ? 'No results yet for this experiment.'
          : 'Could not load per-case results.';
    } finally {
      if (token === resultsRequest) resultsLoading = false;
    }
  }

  function toggleResultTurns(): void {
    resultsIncludeTurns = !resultsIncludeTurns;
    void loadResults(resultsIncludeTurns);
  }

  // A case carries a multi-turn conversation when it has any persisted turns.
  $: resultsHasConversations = (resultsData?.cases ?? []).some((c) => c.turns.length > 0);
  $: resultsPassCount = (resultsData?.cases ?? []).filter((c) => c.matched === true).length;
</script>

<section>
  <div class="flex items-baseline justify-between mb-4">
    <div class="flex items-baseline gap-3">
      <h2 class="text-lg font-semibold">Per-case results</h2>
      {#if resultsData}
        <span class="text-xs text-text-3 font-mono" data-numeric>
          {resultsPassCount}/{resultsData.total} passed
          {#if resultsData.iteration !== null}· iter {resultsData.iteration}{/if}
        </span>
      {/if}
    </div>
    {#if resultsHasConversations}
      <button
        type="button"
        class="text-xs text-text-2 underline-offset-2 hover:text-text-1 hover:underline"
        on:click={toggleResultTurns}
      >
        {resultsIncludeTurns ? 'Hide turns' : 'Expand turns'}
      </button>
    {/if}
  </div>

  {#if resultsLoading && !resultsData}
    <div class="space-y-3">
      {#each Array(3) as _}
        <div class="h-20 rounded-md border border-border bg-surface animate-pulse"></div>
      {/each}
    </div>
  {:else if resultsError}
    <div class="rounded-lg border border-border bg-surface px-6 py-8 text-center text-text-2">
      {resultsError}
    </div>
  {:else if resultsData && resultsData.cases.length === 0}
    <div class="rounded-lg border border-border bg-surface px-6 py-12 text-center text-text-2">
      No cases recorded for the best iteration yet.
    </div>
  {:else if resultsData}
    <div class="space-y-3" class:opacity-60={resultsLoading}>
      {#each resultsData.cases as c (c.case_id)}
        <CaseResultRow result={c} {workspaceId} />
      {/each}
    </div>
  {/if}
</section>

<!--
  Pairwise panel: read-only view of an experiment's pairwise judgments.

  Three sections, all GET (no writes here — running a tournament needs a
  judge entrypoint and belongs in a follow-up):
    1. Judge calibration — LLM-vs-human agreement, overall + per rubric version.
    2. Tournaments — the ranking (Elo / Bradley-Terry) of the latest run.
    3. Verdicts — the raw pairwise preferences, filterable by judge kind.

  Self-contained and lazy: the parent renders it only when the tab is open,
  and we fetch on mount. A request token guards against out-of-order
  responses overwriting newer state — same discipline as the Funnel tab.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import {
    api,
    ApiError,
    type PairwiseCalibration,
    type PairwiseVerdict,
    type Tournament
  } from '$lib/api/client';

  export let workspaceId: string;
  export let experimentId: string;

  let calibration: PairwiseCalibration | null = null;
  let tournaments: Tournament[] = [];
  let verdicts: PairwiseVerdict[] = [];
  let loading = true;
  let error: string | null = null;

  // Judge-kind filter for the verdicts list (client-side re-fetch).
  let judgeFilter: '' | 'llm' | 'human' = '';
  // Which filter the current `verdicts` list reflects. Starts at the initial
  // unfiltered load so the reactive below only fires on an actual user change
  // — not on the mount that already fetched the unfiltered list.
  let loadedFilter: '' | 'llm' | 'human' = '';
  let verdictsToken = 0;

  $: latestTournament = tournaments[0] ?? null;
  $: pct = (n: number) => `${(n * 100).toFixed(1)}%`;

  onMount(load);

  async function load(): Promise<void> {
    loading = true;
    error = null;
    try {
      // Calibration and tournaments are independent — fetch together.
      const [cal, trs, vs] = await Promise.all([
        api.verdictCalibration(workspaceId, experimentId).catch((e) => {
          // No verdicts yet → calibration 404s; treat as "nothing to show".
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        }),
        api.listTournaments(workspaceId, experimentId),
        api.listVerdicts(workspaceId, experimentId)
      ]);
      calibration = cal;
      tournaments = trs;
      verdicts = vs;
    } catch (e) {
      error =
        e instanceof ApiError ? e.detail : 'Could not load pairwise data for this experiment.';
    } finally {
      loading = false;
    }
  }

  async function reloadVerdicts(filter: '' | 'llm' | 'human'): Promise<void> {
    const token = ++verdictsToken;
    try {
      const vs = await api.listVerdicts(
        workspaceId,
        experimentId,
        filter ? { judgeKind: filter } : {}
      );
      if (token !== verdictsToken) return; // a newer filter superseded this one
      verdicts = vs;
      loadedFilter = filter;
    } catch {
      // Keep the prior list on a transient filter error; the main `error`
      // banner already covers a hard failure on initial load.
    }
  }

  // Re-fetch only when the user actually changes the filter away from what the
  // list currently reflects — the initial unfiltered list came from load().
  $: if (judgeFilter !== loadedFilter) void reloadVerdicts(judgeFilter);
</script>

{#if loading}
  <div class="rounded-lg border border-border bg-surface px-5 py-8 text-center text-sm text-text-3">
    Loading pairwise data…
  </div>
{:else if error}
  <div class="rounded-lg border border-danger/30 bg-surface px-5 py-4 text-sm text-danger">
    {error}
  </div>
{:else}
  <div class="space-y-8">
    <!-- 1. Calibration -->
    <section>
      <h3 class="text-xs uppercase tracking-wide text-text-3 mb-3">Judge calibration</h3>
      {#if calibration && calibration.compared_pairs > 0}
        <div class="rounded-lg border border-border bg-surface px-5 py-4">
          <div class="flex items-baseline gap-6">
            <div>
              <div class="text-2xl font-semibold" data-numeric>
                {pct(calibration.agreement_rate)}
              </div>
              <div class="text-xs text-text-3">LLM ↔ human agreement</div>
            </div>
            <div class="text-sm text-text-2" data-numeric>
              {calibration.agreements}/{calibration.compared_pairs} agree
              <span class="text-text-3">· {calibration.disagreements} disagree</span>
            </div>
          </div>

          {#if calibration.by_rubric_version.length > 0}
            <table class="w-full text-sm mt-4">
              <thead class="text-text-3 text-xs uppercase tracking-wide">
                <tr>
                  <th class="text-left py-1.5 font-medium">Rubric ver.</th>
                  <th class="text-right py-1.5 font-medium">Pairs</th>
                  <th class="text-right py-1.5 font-medium">Agree</th>
                  <th class="text-right py-1.5 font-medium">Rate</th>
                </tr>
              </thead>
              <tbody>
                {#each calibration.by_rubric_version as cell}
                  <tr class="border-t border-border">
                    <td class="py-1.5" data-numeric>{cell.rubric_version ?? '—'}</td>
                    <td class="text-right py-1.5" data-numeric>{cell.compared_pairs}</td>
                    <td class="text-right py-1.5" data-numeric>{cell.agreements}</td>
                    <td class="text-right py-1.5" data-numeric>{pct(cell.agreement_rate)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </div>
      {:else}
        <p class="text-sm text-text-3">
          No paired LLM + human verdicts yet — calibration needs both judges on the same pair.
        </p>
      {/if}
    </section>

    <!-- 2. Tournament ranking -->
    <section>
      <h3 class="text-xs uppercase tracking-wide text-text-3 mb-3">
        Tournament ranking
        {#if tournaments.length > 0}
          <span class="font-mono normal-case text-text-3" data-numeric>· {tournaments.length}</span>
        {/if}
      </h3>
      {#if latestTournament}
        <div class="rounded-lg border border-border bg-surface overflow-hidden">
          <div
            class="flex items-baseline justify-between gap-3 px-5 py-3 border-b border-border bg-surface-2 text-xs text-text-3"
          >
            <span class="uppercase tracking-wide">
              {latestTournament.method} · {latestTournament.strategy}
            </span>
            <span data-numeric
              >{latestTournament.n_comparisons} comparisons · {new Date(
                latestTournament.created_at
              ).toLocaleString()}</span
            >
          </div>
          <table class="w-full text-sm">
            <thead class="text-text-3 text-xs uppercase tracking-wide">
              <tr>
                <th class="text-left px-5 py-2 w-12 font-medium">#</th>
                <th class="text-left px-5 py-2 font-medium">Candidate</th>
                <th class="text-right px-5 py-2 font-medium">Score</th>
                <th class="text-right px-5 py-2 font-medium">W/L/T</th>
              </tr>
            </thead>
            <tbody>
              {#each latestTournament.ranking as row (row.candidate_id)}
                <tr class="border-t border-border">
                  <td class="px-5 py-2 font-mono text-text-3" data-numeric>{row.rank}</td>
                  <td class="px-5 py-2 font-mono">{row.candidate_id}</td>
                  <td class="text-right px-5 py-2" data-numeric>{row.score.toFixed(3)}</td>
                  <td class="text-right px-5 py-2 text-text-2" data-numeric>
                    {row.wins}/{row.losses}/{row.ties}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <p class="text-sm text-text-3">No tournaments run for this experiment yet.</p>
      {/if}
    </section>

    <!-- 3. Verdicts -->
    <section>
      <div class="flex items-baseline justify-between mb-3">
        <h3 class="text-xs uppercase tracking-wide text-text-3">
          Verdicts
          <span class="font-mono normal-case text-text-3" data-numeric>· {verdicts.length}</span>
        </h3>
        <select
          bind:value={judgeFilter}
          class="text-xs border border-border rounded bg-surface px-2 py-1 text-text-2"
          aria-label="Filter by judge kind"
        >
          <option value="">All judges</option>
          <option value="llm">LLM</option>
          <option value="human">Human</option>
        </select>
      </div>
      {#if verdicts.length > 0}
        <div class="rounded-lg border border-border bg-surface overflow-hidden">
          <table class="w-full text-sm">
            <thead class="text-text-3 text-xs uppercase tracking-wide bg-surface-2">
              <tr>
                <th class="text-left px-4 py-2 font-medium">Judge</th>
                <th class="text-left px-4 py-2 font-medium">Preferred</th>
                <th class="text-right px-4 py-2 font-medium">Margin</th>
                <th class="text-left px-4 py-2 font-medium">Rationale</th>
              </tr>
            </thead>
            <tbody>
              {#each verdicts as v (v.id)}
                <tr class="border-t border-border align-top">
                  <td class="px-4 py-2 whitespace-nowrap">
                    <span
                      class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium text-text-2 bg-surface-2"
                    >
                      {v.judge_kind}
                    </span>
                    <span class="text-text-3 font-mono text-xs ml-1">{v.judge_id}</span>
                  </td>
                  <td class="px-4 py-2 font-mono uppercase text-text-1">{v.preferred}</td>
                  <td class="text-right px-4 py-2" data-numeric>{v.margin.toFixed(2)}</td>
                  <td class="px-4 py-2 text-text-2">{v.rationale ?? '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <p class="text-sm text-text-3">No verdicts match this filter.</p>
      {/if}
    </section>
  </div>
{/if}

<script lang="ts">
  import { page } from '$app/stores';
  import { onDestroy } from 'svelte';
  import { invalidateAll, goto } from '$app/navigation';
  import { api, ApiError, type ArenaVariant, type ArenaRound } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import Tabs from '$lib/components/ui/Tabs.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import StatusDot from '$lib/components/ui/StatusDot.svelte';
  import TextField from '$lib/components/ui/TextField.svelte';
  import type { PageData } from './$types';

  export let data: PageData;

  $: workspaceId = $page.params.workspace as string;
  $: arenaId = $page.params.arena as string;
  $: arena = data.arena;
  $: variants = data.variants;
  $: rounds = data.rounds;
  $: bundle = data.bundle;

  type Tab = 'leaderboard' | 'matrix' | 'rounds' | 'variants';
  let tab: Tab = 'leaderboard';
  function setTab(id: string) {
    if (id === 'leaderboard' || id === 'matrix' || id === 'rounds' || id === 'variants') tab = id;
  }

  // --- Live poll: any variant still preparing, or the latest round still
  // running, means there's work in flight — refresh until it settles.
  $: preparing = variants.some((v: ArenaVariant) => v.state === 'preparing');
  $: latestRound = rounds.length > 0 ? rounds[rounds.length - 1] : null;
  $: roundRunning = latestRound?.state === 'running';
  $: isActive = preparing || roundRunning;

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  function startPoll() {
    if (pollTimer || typeof window === 'undefined') return;
    pollTimer = setInterval(() => void invalidateAll(), 2500);
  }
  function stopPoll() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
  $: if (isActive) startPoll();
  else stopPoll();
  onDestroy(stopPoll);

  // --- Register variant ----------------------------------------------
  let showAddVariant = false;
  let newVariantName = '';
  let newVariantRef = '';
  let newVariantHypothesis = '';
  let addingVariant = false;
  let addVariantError: string | null = null;

  async function submitAddVariant() {
    if (!newVariantName.trim() || !newVariantRef.trim()) {
      addVariantError = 'Name and git ref are required.';
      return;
    }
    addingVariant = true;
    addVariantError = null;
    try {
      await api.registerArenaVariant(workspaceId, arenaId, {
        name: newVariantName.trim(),
        git_ref: newVariantRef.trim(),
        hypothesis: newVariantHypothesis.trim() || undefined
      });
      toast.success('Variant registered', `"${newVariantName}" is preparing its worktree.`);
      showAddVariant = false;
      newVariantName = '';
      newVariantRef = '';
      newVariantHypothesis = '';
      await invalidateAll();
    } catch (err) {
      addVariantError = err instanceof ApiError ? err.detail : String(err);
    } finally {
      addingVariant = false;
    }
  }

  // --- Launch round -----------------------------------------------------
  let launchingRound = false;
  $: readyVariants = variants.filter((v: ArenaVariant) => v.state === 'ready');

  // Cost preview before firing a round — so "Launch (3 ready)" isn't a blind
  // spend. Re-estimates when the ready set changes; a black-box agent yields a
  // null estimate, which we render as "not priced" rather than $0.
  let costEstimate: { estimated_usd: number | null; basis: string } | null = null;
  let estimatingCost = false;
  let lastEstimatedKey = '';

  $: void maybeEstimate(readyVariants.map((v) => v.id).join(','));

  async function maybeEstimate(key: string) {
    if (key === lastEstimatedKey) return;
    lastEstimatedKey = key;
    if (!key) {
      costEstimate = null;
      return;
    }
    estimatingCost = true;
    try {
      costEstimate = await api.estimateRoundCost(workspaceId, arenaId, {});
    } catch {
      costEstimate = null; // estimate is a nicety; never block the launch on it
    } finally {
      estimatingCost = false;
    }
  }

  function fmtEstimate(usd: number | null): string {
    if (usd === null) return 'not priced';
    return usd < 0.01 ? `~$${usd.toFixed(4)}` : `~$${usd.toFixed(2)}`;
  }

  async function launchRound() {
    launchingRound = true;
    try {
      const round = await api.launchArenaRound(workspaceId, arenaId, {});
      toast.success(
        'Round launched',
        `Round ${round.index} — ${round.entries.length} variant(s) running.`
      );
      await invalidateAll();
    } catch (err) {
      toast.error('Launch failed', err instanceof ApiError ? err.detail : String(err));
    } finally {
      launchingRound = false;
    }
  }

  // --- Promote winner -----------------------------------------------------
  let showPromote: ArenaVariant | null = null;
  let promoting = false;
  let promoteResult: { suggested_commands: string[] } | null = null;

  async function submitPromote() {
    if (!showPromote) return;
    promoting = true;
    try {
      const res = await api.promoteArenaVariant(workspaceId, arenaId, showPromote.id);
      promoteResult = res;
      toast.success('Winner promoted', `"${showPromote.name}" marked as the arena winner.`);
      await invalidateAll();
    } catch (err) {
      toast.error('Promote failed', err instanceof ApiError ? err.detail : String(err));
    } finally {
      promoting = false;
    }
  }

  function closePromote() {
    showPromote = null;
    promoteResult = null;
  }

  function roundStateForVariant(round: ArenaRound, variantId: string): string {
    return round.entries.find((e) => e.variant_id === variantId)?.status ?? '—';
  }
</script>

<svelte:head>
  <title>{arena.name} · Arena · {data.workspace.name}</title>
</svelte:head>

<div class="page">
  <nav class="breadcrumb">
    <a href={`/${workspaceId}/arenas`}>Arenas</a>
    <span>/</span>
    <span>{arena.name}</span>
  </nav>

  <header class="head">
    <div>
      <div class="title-row">
        <h1>{arena.name}</h1>
        <span class="state">
          <StatusDot state={arena.state} />
          <span class="state-label">{arena.state}</span>
        </span>
      </div>
      <p class="sub">{arena.goal}</p>
    </div>
    <div class="head-right">
      {#if arena.winner_variant_id}
        <Badge tone="ok"
          >Winner: {variants.find((v: ArenaVariant) => v.id === arena.winner_variant_id)?.name ??
            arena.winner_variant_id}</Badge
        >
      {/if}
      <Button variant="ghost" on:click={() => (showAddVariant = true)}>Add variant</Button>
      <div class="launch-group">
        {#if readyVariants.length > 0}
          <span class="cost-preview font-mono" data-numeric title={costEstimate?.basis ?? ''}>
            {estimatingCost ? 'estimating…' : fmtEstimate(costEstimate?.estimated_usd ?? null)}
          </span>
        {/if}
        <Button
          variant="brand"
          loading={launchingRound}
          disabled={readyVariants.length === 0}
          on:click={launchRound}>Launch round ({readyVariants.length} ready)</Button
        >
      </div>
    </div>
  </header>

  <div class="meta-row">
    <span class="meta-item mono">round {arena.current_round}</span>
    <span class="meta-item mono">{arena.objective_metric}</span>
    <span class="meta-item mono dim">{arena.repo_path}</span>
  </div>

  <Tabs
    tabs={[
      { id: 'leaderboard', label: 'Leaderboard' },
      { id: 'matrix', label: 'Matrix' },
      { id: 'rounds', label: `Rounds (${rounds.length})` },
      { id: 'variants', label: `Variants (${variants.length})` }
    ]}
    active={tab}
    on:change={(e) => setTab(e.detail)}
  />

  <div class="tab-body">
    {#if tab === 'leaderboard'}
      {#if !bundle || bundle.leaderboard.length === 0}
        <div class="empty">
          <p class="empty-title">No scored variants yet</p>
          <p class="empty-sub">
            {#if readyVariants.length === 0}
              Add a variant and wait for its worktree to prepare, then launch a round.
            {:else}
              Launch a round — the leaderboard fills in once it completes.
            {/if}
          </p>
        </div>
      {:else}
        {#if bundle.convergence.converged}
          <div class="converged-banner">
            <Badge tone="ok">Converged</Badge>
            <span
              >Best value hasn't moved by more than {bundle.convergence.min_delta} over the last
              {bundle.convergence.patience} rounds — probably safe to stop trying new variants.</span
            >
          </div>
        {/if}
        <div class="card table-wrap">
          <table>
            <thead>
              <tr>
                <th class="l">#</th>
                <th class="l">Variant</th>
                <th class="r">{bundle.arena.objective_metric}</th>
                <th class="r">Δ vs best</th>
                <th class="r">Cost</th>
                <th class="r"></th>
              </tr>
            </thead>
            <tbody>
              {#each bundle.leaderboard as row (row.variant_id)}
                {@const variant = variants.find((v: ArenaVariant) => v.id === row.variant_id)}
                <tr>
                  <td class="mono dim">{row.rank}</td>
                  <td>
                    <span class="variant-name">{row.name}</span>
                    {#if variant?.hypothesis}
                      <span class="variant-hyp">{variant.hypothesis}</span>
                    {/if}
                  </td>
                  <td class="r mono" data-numeric>{row.primary_value?.toFixed(3) ?? '—'}</td>
                  <td class="r mono" data-numeric class:dim={row.rank === 1}>
                    {row.rank === 1 ? '—' : (row.delta_vs_best?.toFixed(3) ?? '—')}
                  </td>
                  <td class="r mono dim" data-numeric>
                    {row.cost_usd !== null ? `$${row.cost_usd.toFixed(4)}` : '—'}
                  </td>
                  <td class="r">
                    {#if arena.winner_variant_id !== row.variant_id}
                      <Button
                        variant="ghost"
                        size="sm"
                        on:click={() => variant && (showPromote = variant)}>Promote</Button
                      >
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {:else if tab === 'matrix'}
      {#if !bundle || bundle.pairwise_vs_best.length === 0}
        <div class="empty">
          <p class="empty-title">Nothing to compare yet</p>
          <p class="empty-sub">The matrix needs at least two scored variants in the same round.</p>
        </div>
      {:else}
        <div class="matrix-list">
          {#each bundle.pairwise_vs_best as pw (pw.variant_id)}
            <div class="card matrix-card">
              <div class="matrix-head">
                <span class="variant-name">{pw.name}</span>
                <span class="dim">vs best</span>
                <Badge tone={pw.winner ? 'ok' : 'neutral'}>{pw.recommendation_kind}</Badge>
              </div>
              {#if pw.metrics_diff.length > 0}
                <table class="matrix-metrics">
                  <thead>
                    <tr>
                      <th class="l">Metric</th>
                      <th class="r">Best</th>
                      <th class="r">{pw.name}</th>
                      <th class="r">Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each pw.metrics_diff as m (m.name)}
                      <tr>
                        <td class="mono dim">{m.name}</td>
                        <td class="r mono" data-numeric>{m.a?.toFixed(3) ?? '—'}</td>
                        <td class="r mono" data-numeric>{m.b?.toFixed(3) ?? '—'}</td>
                        <td class="r mono dim" data-numeric>{m.delta?.toFixed(3) ?? '—'}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
              {#if Object.keys(pw.only_this).length > 0}
                <p class="fm-line">
                  <span class="fm-label">Only in {pw.name}:</span>
                  {Object.entries(pw.only_this)
                    .map(([k, v]) => `${k} (${v})`)
                    .join(', ')}
                </p>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {:else if tab === 'rounds'}
      {#if rounds.length === 0}
        <div class="empty">
          <p class="empty-title">No rounds yet</p>
          <p class="empty-sub">Launch a round once at least one variant is ready.</p>
        </div>
      {:else}
        <div class="card table-wrap">
          <table>
            <thead>
              <tr>
                <th class="l">Round</th>
                <th class="l">State</th>
                <th class="l">Entries</th>
              </tr>
            </thead>
            <tbody>
              {#each [...rounds].reverse() as round (round.id)}
                <tr>
                  <td class="mono">{round.index}</td>
                  <td>
                    <span class="state">
                      <StatusDot state={round.state} />
                      <span class="state-label">{round.state}</span>
                    </span>
                  </td>
                  <td>
                    {#each round.entries as entry (entry.variant_id)}
                      {@const v = variants.find((x: ArenaVariant) => x.id === entry.variant_id)}
                      <span class="entry-chip mono">
                        {v?.name ?? entry.variant_id}: {entry.status}
                      </span>
                    {/each}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {:else if tab === 'variants'}
      <div class="card table-wrap">
        <table>
          <thead>
            <tr>
              <th class="l">Variant</th>
              <th class="l">State</th>
              <th class="l">Ref</th>
              <th class="l">Hypothesis</th>
            </tr>
          </thead>
          <tbody>
            {#each variants as v (v.id)}
              <tr>
                <td class="variant-name">{v.name}</td>
                <td>
                  <span class="state">
                    <StatusDot state={v.state} />
                    <span class="state-label">{v.state}</span>
                  </span>
                  {#if v.state === 'failed' && v.error}
                    <p class="error-line">{v.error}</p>
                  {/if}
                </td>
                <td class="mono dim">{v.git_ref}</td>
                <td class="dim">{v.hypothesis ?? '—'}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</div>

<Modal open={showAddVariant} title="Add variant" on:close={() => (showAddVariant = false)}>
  <form class="flex flex-col gap-3" on:submit|preventDefault={submitAddVariant}>
    <TextField label="Name" bind:value={newVariantName} placeholder="elevenlabs" required />
    <TextField
      label="Git ref"
      bind:value={newVariantRef}
      placeholder="arena/elevenlabs"
      hint="An existing branch, tag, or commit in the arena's repo."
      required
    />
    <TextField
      label="Hypothesis (optional)"
      bind:value={newVariantHypothesis}
      multiline
      rows={2}
      placeholder="Why do you expect this variant to win?"
    />
    {#if addVariantError}
      <p class="text-sm text-danger">{addVariantError}</p>
    {/if}
    <div class="flex justify-end gap-2 pt-1">
      <Button variant="ghost" on:click={() => (showAddVariant = false)}>Cancel</Button>
      <Button variant="primary" type="submit" loading={addingVariant}>Register</Button>
    </div>
  </form>
</Modal>

<Modal open={showPromote !== null} title="Promote winner" on:close={closePromote}>
  {#if showPromote}
    <div class="flex flex-col gap-3">
      {#if !promoteResult}
        <p class="text-sm">
          Mark <strong>{showPromote.name}</strong> as the arena's winner. This does not touch git — you'll
          get copy-paste commands to merge it yourself.
        </p>
        <div class="flex justify-end gap-2 pt-1">
          <Button variant="ghost" on:click={closePromote}>Cancel</Button>
          <Button variant="primary" loading={promoting} on:click={submitPromote}>Promote</Button>
        </div>
      {:else}
        <p class="text-sm text-text-2">Suggested commands:</p>
        <pre class="commands">{promoteResult.suggested_commands.join('\n')}</pre>
        <div class="flex justify-end pt-1">
          <Button variant="primary" on:click={closePromote}>Done</Button>
        </div>
      {/if}
    </div>
  {/if}
</Modal>

<style>
  .page {
    padding: 2.5rem 3rem;
    max-width: 72rem;
    margin: 0 auto;
  }
  .breadcrumb {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-bottom: 1.25rem;
    display: flex;
    align-items: center;
    gap: 0.375rem;
  }
  .breadcrumb a {
    color: var(--color-text-3);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .breadcrumb a:hover {
    color: var(--color-text-1);
  }
  .head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.75rem;
  }
  .title-row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
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
    max-width: 40rem;
  }
  .head-right {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-shrink: 0;
  }
  .state {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  .state-label {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--color-text-2);
  }
  .meta-row {
    display: flex;
    gap: 1rem;
    margin-bottom: 1.25rem;
  }
  .meta-item {
    font-size: var(--text-xs);
    color: var(--color-text-3);
  }
  .meta-item.dim {
    max-width: 28rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .launch-group {
    display: inline-flex;
    align-items: center;
    gap: 0.6rem;
  }
  /* Cost preview reads quiet next to the launch button — a spend heads-up, not
     an alarm. Its title carries the estimate basis on hover. */
  .cost-preview {
    font-size: var(--text-xs);
    color: var(--color-text-3);
    white-space: nowrap;
  }
  .tab-body {
    margin-top: 1.25rem;
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
  .variant-name {
    display: block;
    font-weight: 500;
    color: var(--color-text-1);
  }
  .variant-hyp {
    display: block;
    font-size: var(--text-xs);
    color: var(--color-text-3);
    margin-top: 0.15rem;
    max-width: 26rem;
  }
  .error-line {
    font-size: var(--text-2xs);
    color: var(--color-bad);
    margin-top: 0.2rem;
  }
  .entry-chip {
    display: inline-block;
    font-size: var(--text-2xs);
    color: var(--color-text-2);
    background: var(--color-surface-2);
    border-radius: var(--radius-sm);
    padding: 0.15rem 0.4rem;
    margin: 0.15rem 0.3rem 0.15rem 0;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.4rem;
    padding: 3rem 1.5rem;
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
    max-width: 30rem;
  }
  .converged-banner {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: var(--text-sm);
    color: var(--color-text-2);
    background: var(--color-ok-subtle);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 0.6rem 0.9rem;
    margin-bottom: 0.75rem;
  }
  .matrix-list {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .matrix-card {
    padding: 1rem 1.25rem;
  }
  .matrix-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }
  .matrix-metrics {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-xs);
  }
  .matrix-metrics th {
    text-align: left;
    color: var(--color-text-3);
    font-weight: 500;
    padding: 0.3rem 0.5rem;
  }
  .matrix-metrics th.r,
  .matrix-metrics td.r {
    text-align: right;
  }
  .matrix-metrics td {
    padding: 0.3rem 0.5rem;
    border-top: 1px solid var(--color-border);
  }
  .fm-line {
    font-size: var(--text-xs);
    color: var(--color-text-2);
    margin-top: 0.6rem;
  }
  .fm-label {
    color: var(--color-text-3);
    margin-right: 0.3rem;
  }
  .commands {
    background: var(--color-surface-2);
    border-radius: var(--radius-md);
    padding: 0.75rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    white-space: pre-wrap;
    word-break: break-all;
  }
</style>

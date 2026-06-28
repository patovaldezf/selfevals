<script lang="ts">
  import type { PageData } from './$types';
  import type { LayoutData } from '../../../$types';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import {
    api,
    ApiError,
    type AnalysisAssignment,
    type AnalysisProposedMode,
    type AnalysisIngestSummary
  } from '$lib/api/client';
  import { toast } from '$lib/stores/toasts';
  import Button from '$lib/components/ui/Button.svelte';
  import Select from '$lib/components/ui/Select.svelte';
  import TextField from '$lib/components/ui/TextField.svelte';
  import Badge from '$lib/components/ui/Badge.svelte';
  import Icon from '$lib/components/ui/Icon.svelte';
  import { CheckCircle2, ArrowRight, AlertTriangle } from 'lucide-svelte';

  export let data: PageData & LayoutData;

  $: wsId = data.workspace.id;
  $: bundle = data.bundle;

  // Per-trace coding state. Each trace gets a chosen mode: either an existing
  // taxonomy id, the sentinel "__new__" (propose), or "" (skip). A proposed
  // mode is composed once and shared by every trace that picks "__new__".
  let choice: Record<string, string> = {};
  let note: Record<string, string> = {};

  // New-mode proposal (single, shared).
  let proposeSlug = '';
  let proposeTitle = '';
  let proposeDefinition = '';

  let submitting = false;
  let summary: AnalysisIngestSummary | null = null;

  $: modeOptions = [
    { value: '', label: '— skip —' },
    ...bundle.taxonomy.map((t) => ({ value: t.id, label: `${t.title} (${t.status})` })),
    { value: '__new__', label: '+ propose new mode' }
  ];

  $: anyNew = Object.values(choice).some((c) => c === '__new__');
  $: coded = Object.values(choice).filter((c) => c && c !== '').length;

  function toggleAll() {
    const sp = new URLSearchParams($page.url.searchParams);
    if (data.all) sp.delete('all');
    else sp.set('all', 'true');
    goto(`?${sp.toString()}`, { keepFocus: true, noScroll: true });
  }

  function buildResult(): {
    assignments: AnalysisAssignment[];
    proposed_modes: AnalysisProposedMode[];
  } {
    const assignments: AnalysisAssignment[] = [];
    for (const t of bundle.traces) {
      const c = choice[t.trace_id];
      if (!c) continue;
      if (c === '__new__') {
        assignments.push({
          trace_id: t.trace_id,
          new_mode_slug: proposeSlug.trim(),
          open_note: note[t.trace_id]?.trim() || undefined
        });
      } else {
        assignments.push({
          trace_id: t.trace_id,
          mode_id: c,
          open_note: note[t.trace_id]?.trim() || undefined
        });
      }
    }
    const proposed_modes: AnalysisProposedMode[] = anyNew
      ? [
          {
            slug: proposeSlug.trim(),
            title: proposeTitle.trim(),
            definition: proposeDefinition.trim()
          }
        ]
      : [];
    return { assignments, proposed_modes };
  }

  $: canSubmit =
    coded > 0 &&
    (!anyNew || (proposeSlug.trim() && proposeTitle.trim() && proposeDefinition.trim())) &&
    !submitting;

  async function submit() {
    submitting = true;
    try {
      summary = await api.analysisIngest(wsId, data.experimentId, buildResult());
      toast.success(
        'Analysis ingested',
        `${summary.assignments_applied} assignments · ${summary.created_candidates.length} new candidates`
      );
    } catch (err) {
      toast.error('Ingest failed', err instanceof ApiError ? err.detail : String(err));
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head>
  <title>Analyze · {data.workspace.name}</title>
</svelte:head>

<div class="px-12 py-10 max-w-5xl mx-auto">
  <nav class="text-xs text-text-3 mb-6 flex items-center gap-1.5" aria-label="Breadcrumb">
    <a class="hover:text-text-1" href={`/${wsId}/experiments`}>experiments</a>
    <span aria-hidden="true">/</span>
    <a class="hover:text-text-1" href={`/${wsId}/experiments/${data.experimentId}`}>experiment</a>
    <span aria-hidden="true">/</span>
    <span class="text-text-2">analyze</span>
  </nav>

  <header class="mb-7 flex items-end justify-between gap-4">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Error analysis</h1>
      <p class="text-text-2 mt-1.5 text-sm">
        Code each failed trace against the taxonomy, or propose a new mode. Ingesting closes the
        loop — new candidates land in Failure modes, ready to promote.
      </p>
    </div>
    <Button variant="ghost" size="sm" on:click={toggleAll}>
      {data.all ? 'Failed only' : 'Include passing'}
    </Button>
  </header>

  {#if summary}
    <div class="mb-6 rounded-lg border border-border bg-surface p-5">
      <h2 class="text-sm font-semibold mb-2">Ingest summary</h2>
      <div class="flex flex-wrap gap-x-8 gap-y-2 text-sm">
        <span
          ><span class="font-mono tabular-nums" data-numeric>{summary.assignments_applied}</span> assignments
          applied</span
        >
        <span
          ><span class="font-mono tabular-nums" data-numeric
            >{summary.created_candidates.length}</span
          > new candidates</span
        >
        <span
          ><span class="font-mono tabular-nums" data-numeric
            >{summary.updated_candidates.length}</span
          > re-seen</span
        >
      </div>
      {#if summary.created_candidates.length}
        <a
          href={`/${wsId}/failure-modes?status=candidate`}
          class="mt-3 inline-flex items-center gap-1 text-sm text-brand-strong hover:underline underline-offset-2"
          style:color="var(--color-brand-strong)"
        >
          Review &amp; promote new candidates
          <Icon icon={ArrowRight} size={14} />
        </a>
      {/if}
    </div>
  {/if}

  {#if bundle.traces.length === 0}
    <div class="empty">
      <Icon icon={CheckCircle2} size={22} />
      <p class="empty-title">{data.all ? 'No traces to analyze' : 'No failed traces'}</p>
      <p class="empty-sub">
        {data.all
          ? 'This experiment has no persisted traces yet. Run it with persist_traces enabled.'
          : 'Nothing failed in this experiment — or traces were not persisted. Toggle “Include passing” to see all.'}
      </p>
    </div>
  {:else}
    <div class="flex flex-col gap-3">
      {#each bundle.traces as t (t.trace_id)}
        <div class="rounded-lg border border-border bg-surface p-5">
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <Badge tone="bad" size="sm">{t.grade.label}</Badge>
                {#if t.eval_case_id}<span class="font-mono text-xs text-text-3"
                    >{t.eval_case_id}</span
                  >{/if}
              </div>
              {#if t.first_error_span}
                <p class="error-line">
                  <Icon icon={AlertTriangle} size={13} />
                  {t.first_error_span.kind}: {t.first_error_span.error ?? t.first_error_span.name}
                </p>
              {/if}
              {#if t.grade.judge_reason}
                <p class="mt-1 text-sm text-text-2">{t.grade.judge_reason}</p>
              {/if}
            </div>
            <a class="trace-link shrink-0" href={`/${wsId}/traces/${t.trace_id}`}>
              open trace
              <Icon icon={ArrowRight} size={13} />
            </a>
          </div>

          {#if t.transcript.length}
            <details class="mt-3">
              <summary class="cursor-pointer text-xs text-text-3 hover:text-text-1">
                Transcript ({t.transcript.length} messages)
              </summary>
              <div class="mt-2 flex flex-col gap-2">
                {#each t.transcript as m}
                  <div class="rounded border border-border bg-surface-2 px-3 py-2">
                    <div class="text-xs font-medium text-text-3 mb-0.5">{m.role}</div>
                    <div class="text-sm text-text-1 whitespace-pre-wrap break-words">
                      {m.content}
                    </div>
                  </div>
                {/each}
              </div>
            </details>
          {/if}

          <!-- Coding controls -->
          <div class="mt-4 grid grid-cols-[1fr_1fr] gap-3 items-end">
            <Select label="Failure mode" options={modeOptions} bind:value={choice[t.trace_id]} />
            <TextField
              label="Note (optional)"
              bind:value={note[t.trace_id]}
              placeholder="What went wrong"
            />
          </div>
        </div>
      {/each}
    </div>

    <!-- New-mode proposal (shown when any trace picks "propose new") -->
    {#if anyNew}
      <div class="mt-4 rounded-lg border border-dashed border-border-strong bg-surface p-5">
        <h2 class="text-sm font-semibold mb-3">Propose new failure mode</h2>
        <div class="grid grid-cols-2 gap-3">
          <TextField label="Slug" bind:value={proposeSlug} placeholder="invented_price" required />
          <TextField
            label="Title"
            bind:value={proposeTitle}
            placeholder="Invented price"
            required
          />
        </div>
        <div class="mt-3">
          <TextField
            label="Definition"
            bind:value={proposeDefinition}
            multiline
            rows={3}
            required
            hint="The testable distinction — what makes a trace this mode and not another."
          />
        </div>
      </div>
    {/if}

    <!-- Submit bar -->
    <div
      class="mt-6 flex items-center justify-between gap-4 sticky bottom-4 rounded-lg border border-border bg-surface px-5 py-3.5 shadow-2"
    >
      <span class="text-sm text-text-2">
        <span class="font-mono tabular-nums" data-numeric>{coded}</span> of
        <span class="font-mono tabular-nums" data-numeric>{bundle.traces.length}</span> coded
      </span>
      <Button variant="primary" loading={submitting} disabled={!canSubmit} on:click={submit}>
        Ingest analysis
      </Button>
    </div>
  {/if}
</div>

<style>
  .error-line {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    margin-top: 0.4rem;
    font-size: var(--text-sm);
    color: var(--color-bad);
  }
  .trace-link {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: var(--text-xs);
    color: var(--color-text-2);
    transition: color var(--dur-fast) var(--ease-out);
  }
  .trace-link:hover {
    color: var(--color-text-1);
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.6rem;
    padding: 3.5rem 1.5rem;
    text-align: center;
    color: var(--color-ok);
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

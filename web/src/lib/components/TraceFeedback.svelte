<!--
  TraceFeedback: attach a human good/bad verdict + notes to this trace.

  LangSmith-parity: a run needs a place to say "this was right / this was
  wrong" and jot why. Self-contained — owns its own fetch + state so it can
  drop into the (legacy) TraceSidebar without migrating it. Reloads the
  annotation list after each submit so the user sees their verdict land.
-->
<script lang="ts">
  import { api, ApiError } from '$lib/api/client';
  import Icon from '$lib/components/ui/Icon.svelte';
  import Button from '$lib/components/ui/Button.svelte';
  import { onMount } from 'svelte';
  import { ThumbsUp, ThumbsDown } from 'lucide-svelte';
  import type { TraceAnnotation } from '$lib/api/types';

  let { workspaceId, traceId }: { workspaceId: string; traceId: string } = $props();

  let annotations = $state<TraceAnnotation[]>([]);
  let verdict = $state<'good' | 'bad' | null>(null);
  let notes = $state('');
  let saving = $state(false);
  let error = $state<string | null>(null);

  onMount(async () => {
    // Load any existing feedback for this trace so the reviewer sees prior
    // verdicts. Best-effort — a fetch failure just leaves the list empty.
    try {
      const list = await api.traceAnnotations(workspaceId, traceId);
      annotations = list.annotations;
    } catch {
      /* no-op: feedback list stays empty */
    }
  });

  async function submit() {
    if (verdict === null || saving) return;
    saving = true;
    error = null;
    try {
      await api.annotateTrace(workspaceId, traceId, {
        verdict,
        notes: notes.trim() || undefined
      });
      const list = await api.traceAnnotations(workspaceId, traceId);
      annotations = list.annotations;
      verdict = null;
      notes = '';
    } catch (e) {
      error = e instanceof ApiError ? String(e.detail) : 'No se pudo guardar la anotación.';
    } finally {
      saving = false;
    }
  }
</script>

<section class="feedback">
  <div class="feedback-head">Feedback</div>

  <div class="verdict-row">
    <button
      type="button"
      class="verdict-btn"
      class:active-good={verdict === 'good'}
      aria-pressed={verdict === 'good'}
      onclick={() => (verdict = verdict === 'good' ? null : 'good')}
    >
      <Icon icon={ThumbsUp} size={15} />
      <span>Bien</span>
    </button>
    <button
      type="button"
      class="verdict-btn"
      class:active-bad={verdict === 'bad'}
      aria-pressed={verdict === 'bad'}
      onclick={() => (verdict = verdict === 'bad' ? null : 'bad')}
    >
      <Icon icon={ThumbsDown} size={15} />
      <span>Mal</span>
    </button>
  </div>

  <textarea
    class="notes"
    bind:value={notes}
    rows="2"
    placeholder="Qué estuvo bien / mal (opcional)…"
  ></textarea>

  {#if error}
    <p class="error">{error}</p>
  {/if}

  <Button variant="secondary" size="sm" disabled={verdict === null || saving} on:click={submit}>
    {saving ? 'Guardando…' : 'Guardar feedback'}
  </Button>

  {#if annotations.length > 0}
    <ul class="ann-list">
      {#each annotations as ann (ann.id)}
        <li class="ann">
          <span class="ann-verdict ann-{ann.verdict}">
            {ann.verdict === 'good' ? 'bien' : ann.verdict === 'bad' ? 'mal' : '—'}
          </span>
          <div class="ann-body">
            {#if ann.notes}<p class="ann-notes">{ann.notes}</p>{/if}
            <span class="ann-meta">{ann.annotator_id}</span>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .feedback {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    padding: 0.9rem 0;
    border-bottom: 1px solid var(--color-border);
    margin-bottom: 0.5rem;
  }
  .feedback-head {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .verdict-row {
    display: flex;
    gap: 0.5rem;
  }
  .verdict-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    flex: 1;
    justify-content: center;
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg);
    color: var(--color-text-2);
    font-size: var(--text-xs);
    transition:
      color var(--dur-fast) var(--ease-out),
      border-color var(--dur-fast) var(--ease-out),
      background var(--dur-fast) var(--ease-out);
  }
  .verdict-btn:hover {
    color: var(--color-text-1);
  }
  .verdict-btn.active-good {
    color: var(--color-ok);
    border-color: color-mix(in oklab, var(--color-ok) 50%, var(--color-border));
    background: color-mix(in oklab, var(--color-ok) 10%, var(--color-bg));
  }
  .verdict-btn.active-bad {
    color: var(--color-bad);
    border-color: color-mix(in oklab, var(--color-bad) 50%, var(--color-border));
    background: color-mix(in oklab, var(--color-bad) 10%, var(--color-bg));
  }
  .notes {
    width: 100%;
    resize: vertical;
    padding: 0.5rem 0.6rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg);
    color: var(--color-text-1);
    font-family: inherit;
    font-size: var(--text-xs);
    line-height: 1.5;
  }
  .notes:focus {
    outline: none;
    border-color: var(--color-brand);
  }
  .error {
    font-size: var(--text-xs);
    color: var(--color-bad);
  }
  .ann-list {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
    margin-top: 0.3rem;
  }
  .ann {
    display: flex;
    gap: 0.5rem;
    align-items: flex-start;
  }
  .ann-verdict {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 0.1rem 0.4rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
    flex-shrink: 0;
  }
  .ann-good {
    color: var(--color-ok);
  }
  .ann-bad {
    color: var(--color-bad);
  }
  .ann-body {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    min-width: 0;
  }
  .ann-notes {
    font-size: var(--text-xs);
    color: var(--color-text-1);
    word-break: break-word;
  }
  .ann-meta {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
</style>

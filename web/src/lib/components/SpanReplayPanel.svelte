<!--
  SpanReplayPanel: the Playground for one llm_call span.

  Re-issues exactly what this span sent (system + messages) against a
  provider/model the user picks, and shows the alternate output next to the
  original — "would another model have done better?". No tools run (no side
  effects). Only offers providers whose SDK + API key are actually present.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError } from '$lib/api/client';
  import Button from '$lib/components/ui/Button.svelte';
  import type { ProviderInfo, SpanReplayResult } from '$lib/api/types';

  let {
    workspaceId,
    traceId,
    spanId,
    originalContent
  }: {
    workspaceId: string;
    traceId: string;
    spanId: string;
    originalContent: string | null;
  } = $props();

  let providers = $state<ProviderInfo[]>([]);
  let provider = $state('');
  let model = $state('');
  let running = $state(false);
  let error = $state<string | null>(null);
  let result = $state<SpanReplayResult | null>(null);

  const current = $derived(providers.find((p) => p.provider === provider));

  onMount(async () => {
    try {
      const list = await api.providers();
      providers = list.providers;
      const firstAvailable = providers.find((p) => p.available) ?? providers[0];
      if (firstAvailable) {
        provider = firstAvailable.provider;
        model = firstAvailable.models[0] ?? '';
      }
    } catch {
      /* leave picker empty; the panel still renders a disabled state */
    }
  });

  function onProviderChange() {
    model = current?.models[0] ?? '';
    result = null;
  }

  async function replay() {
    if (!provider || !model || running) return;
    running = true;
    error = null;
    try {
      result = await api.replaySpan(workspaceId, traceId, spanId, { provider, model });
    } catch (e) {
      error = e instanceof ApiError ? String(e.detail) : 'No se pudo re-correr el span.';
    } finally {
      running = false;
    }
  }

  function fmtCost(c: number | null): string {
    return c === null ? '—' : `$${c.toFixed(4)}`;
  }
</script>

<section class="replay">
  <div class="replay-head">Playground · re-correr con otro modelo</div>

  {#if providers.length === 0}
    <p class="replay-note">No hay proveedores disponibles para replay.</p>
  {:else}
    <div class="replay-controls">
      <select bind:value={provider} onchange={onProviderChange} disabled={running}>
        {#each providers as p (p.provider)}
          <option value={p.provider} disabled={!p.available}>
            {p.provider}{p.available ? '' : ' (sin key/SDK)'}
          </option>
        {/each}
      </select>
      <select bind:value={model} disabled={running || !current}>
        {#each current?.models ?? [] as m (m)}
          <option value={m}>{m}</option>
        {/each}
      </select>
      <Button
        variant="brand"
        size="sm"
        disabled={running || !current?.available || !model}
        on:click={replay}
      >
        {running ? 'Corriendo…' : 'Replay'}
      </Button>
    </div>

    {#if current && !current.available}
      <p class="replay-warn">
        {current.provider}: {current.sdk_installed ? 'falta la API key' : 'falta instalar el SDK'}.
      </p>
    {/if}
    {#if error}<p class="replay-err">{error}</p>{/if}

    {#if result}
      <div class="replay-compare">
        <div class="col">
          <div class="col-head">
            <span class="col-label">original</span>
            <span class="col-model mono">{result.original_provider}/{result.original_model}</span>
          </div>
          <pre class="col-body">{originalContent ?? '(sin contenido)'}</pre>
        </div>
        <div class="col">
          <div class="col-head">
            <span class="col-label alt">alterno</span>
            <span class="col-model mono">{result.provider}/{result.model}</span>
          </div>
          <pre class="col-body">{result.content || '(sin contenido)'}</pre>
          <div class="col-meta mono">
            {result.tokens_input}→{result.tokens_output} tok · {fmtCost(result.cost_usd)} · {result.duration_ms}ms
          </div>
        </div>
      </div>
    {/if}
  {/if}
</section>

<style>
  .replay {
    border: 1px solid var(--color-border);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    padding: 1rem 1.2rem;
    margin-bottom: 1.5rem;
  }
  .replay-head {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.9rem;
  }
  .replay-controls {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .replay-controls select {
    border: 1px solid var(--color-border);
    background: var(--color-bg);
    border-radius: var(--radius-md);
    padding: 0.4rem 0.5rem;
    font-size: var(--text-xs);
    color: var(--color-text-1);
  }
  .replay-note,
  .replay-warn,
  .replay-err {
    font-size: var(--text-xs);
    margin-top: 0.6rem;
  }
  .replay-note {
    color: var(--color-text-3);
  }
  .replay-warn {
    color: var(--color-warn);
  }
  .replay-err {
    color: var(--color-bad);
  }
  .replay-compare {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
    margin-top: 1rem;
  }
  .col {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
  }
  .col-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .col-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .col-label.alt {
    color: var(--color-brand-strong);
  }
  .col-model {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .col-body {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    line-height: 1.55;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 0.6rem 0.7rem;
    margin: 0;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 20rem;
    color: var(--color-text-1);
  }
  .col-meta {
    font-size: var(--text-2xs);
    color: var(--color-text-3);
  }
  .mono {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
</style>

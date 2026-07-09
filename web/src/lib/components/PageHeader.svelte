<!--
  PageHeader — the per-page title block that lives under the global Topbar.
  Consistent eyebrow + title + subtitle rhythm, with an `actions` slot for
  page-level buttons and a `meta` slot for ids/pills under the title. Replaces
  the hand-rolled header each route used to repeat.

  The breadcrumb is NOT here — that's the Topbar's job now. This owns only the
  page's own identity.
-->
<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    eyebrow = null,
    title,
    subtitle = null,
    actions,
    meta
  }: {
    eyebrow?: string | null;
    title: string;
    subtitle?: string | null;
    actions?: Snippet;
    meta?: Snippet;
  } = $props();
</script>

<header class="page-header">
  <div class="lead">
    {#if eyebrow}
      <div class="eyebrow">{eyebrow}</div>
    {/if}
    <h1 class="title">{title}</h1>
    {#if subtitle}
      <p class="subtitle">{subtitle}</p>
    {/if}
    {#if meta}
      <div class="meta">{@render meta()}</div>
    {/if}
  </div>
  {#if actions}
    <div class="actions">{@render actions()}</div>
  {/if}
</header>

<style>
  .page-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1.5rem;
    margin-bottom: 2rem;
  }
  .lead {
    min-width: 0;
  }
  .eyebrow {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
    margin-bottom: 0.4rem;
  }
  .title {
    font-size: var(--text-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: var(--leading-tight);
    color: var(--color-text-1);
  }
  .subtitle {
    margin-top: 0.5rem;
    max-width: 42rem;
    color: var(--color-text-2);
    font-size: var(--text-base);
  }
  .meta {
    margin-top: 0.75rem;
  }
  .actions {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    gap: 0.6rem;
  }
</style>

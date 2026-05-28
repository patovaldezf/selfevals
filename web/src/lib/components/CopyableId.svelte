<!--
  Renders an internal id (ULID-style: ws_..., exp_..., it_..., tr_...,
  run_...) as a compact, copyable chip.

  A5 of FRONTEND_PRODUCT_PLAN.md: human-named entities own the page
  title; the id is reference metadata you click to copy, not the thing
  you read for orientation. Same widget everywhere so the affordance
  is learnable in one place.
-->
<script lang="ts">
  export let id: string;
  /** Optional label shown to assistive tech: "Copy experiment id". */
  export let label: string = 'id';
  /** Visual density — `chip` is the default; `inline` strips the box for use inside dense rows. */
  export let variant: 'chip' | 'inline' = 'chip';

  let copied = false;
  let timeout: ReturnType<typeof setTimeout> | null = null;

  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
    } catch {
      // Clipboard can be blocked in non-secure contexts or by user
      // policy. Surface failure silently for now — the id is still
      // readable on screen — and skip the "copied" affordance so we
      // don't lie about success.
      return;
    }
    copied = true;
    if (timeout) clearTimeout(timeout);
    timeout = setTimeout(() => (copied = false), 1200);
  }
</script>

<button
  type="button"
  on:click|stopPropagation={copy}
  aria-label={`Copy ${label} ${id}`}
  title={copied ? 'Copied' : `Copy ${label}`}
  class={variant === 'chip'
    ? 'group inline-flex items-center gap-1.5 font-mono text-[11px] text-text-3 hover:text-text-1 px-1.5 py-0.5 rounded border border-border bg-surface-2/40 hover:bg-surface-2 hover:border-text-3 transition-colors'
    : 'group inline-flex items-center gap-1 font-mono text-xs text-text-3 hover:text-text-1 transition-colors'}
>
  <span class="truncate">{id}</span>
  <span aria-hidden="true" class="text-text-3 group-hover:text-text-1 text-[10px] shrink-0">
    {copied ? '✓' : '⧉'}
  </span>
</button>

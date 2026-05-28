<!--
  At-a-glance signal for a grader LABEL (pass/fail/partial/error/skipped).

  NOT to be confused with DecisionBadge, which maps experiment DECISION
  outcomes (keep_candidate/reject/...). A grade is the verdict a grader
  emits on a single turn; reusing DecisionBadge here would fall through to
  its grey "—" for every grade — a silent miscolor. This chip owns the
  grade→semantic-color mapping so the conversation reads green/red/amber
  at a glance.

  Foreground colors come from the shared semantic tokens
  (var(--color-success) etc.) so the palette stays consistent with the
  rest of the app; the soft tints match DecisionBadge's pill backgrounds.
-->
<script lang="ts">
  export let grade: string | null;

  const palette: Record<string, { fg: string; bg: string; label: string }> = {
    pass: { fg: 'var(--color-success)', bg: '#E8F5EE', label: 'pass' },
    fail: { fg: 'var(--color-danger)', bg: '#FBE9E9', label: 'fail' },
    partial: { fg: 'var(--color-warning)', bg: '#FBEFD9', label: 'partial' },
    error: { fg: 'var(--color-danger)', bg: '#FBE9E9', label: 'error' },
    skipped: { fg: 'var(--color-text-2)', bg: '#F0F0EE', label: 'skipped' }
  };

  $: meta = grade ? palette[grade] : null;
</script>

{#if meta}
  <span
    class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
    style="color: {meta.fg}; background: {meta.bg};"
  >
    {meta.label}
  </span>
{:else if grade}
  <!-- Unknown label: show it verbatim rather than swallowing it. -->
  <span
    class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium text-text-2 bg-surface-2"
  >
    {grade}
  </span>
{:else}
  <span class="text-text-3 text-xs">—</span>
{/if}

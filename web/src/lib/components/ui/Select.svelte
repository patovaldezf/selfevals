<script lang="ts">
  /** Labeled native select. Native because it's keyboard- and mobile-correct
   *  for free; a custom listbox would be more code for less reliability here.
   *  Options are `{value, label}`; bind `value`. */
  export let label: string | null = null;
  export let value: string = '';
  export let options: { value: string; label: string }[] = [];
  export let placeholder: string | null = null;
  export let hint: string | null = null;
  export let disabled = false;
  export let id: string | null = null;

  const fieldId = id ?? `sel-${Math.random().toString(36).slice(2, 9)}`;
</script>

<div class="flex flex-col gap-1.5">
  {#if label}
    <label for={fieldId} class="text-xs font-medium text-text-2">{label}</label>
  {/if}
  <div class="select-wrap">
    <select id={fieldId} {disabled} class="field" bind:value on:change>
      {#if placeholder}
        <option value="" disabled selected={value === ''}>{placeholder}</option>
      {/if}
      {#each options as opt}
        <option value={opt.value}>{opt.label}</option>
      {/each}
    </select>
    <span class="chevron" aria-hidden="true">▾</span>
  </div>
  {#if hint}
    <p class="text-xs text-text-3">{hint}</p>
  {/if}
</div>

<style>
  .select-wrap {
    position: relative;
  }
  .field {
    width: 100%;
    appearance: none;
    background: var(--color-surface);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-md);
    padding: 0.5rem 2rem 0.5rem 0.7rem;
    font-size: 14px;
    color: var(--color-text-1);
    transition:
      border-color 0.14s ease-out,
      box-shadow 0.14s ease-out;
  }
  .field:focus {
    outline: none;
    border-color: var(--color-brand);
    box-shadow: 0 0 0 3px var(--color-brand-subtle);
  }
  .field:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .chevron {
    position: absolute;
    right: 0.7rem;
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
    font-size: 10px;
    color: var(--color-text-3);
  }
</style>

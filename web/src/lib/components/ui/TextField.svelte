<script lang="ts">
  /** Labeled text input / textarea with optional hint + error. Binds `value`.
   *  Set `multiline` for a textarea (specs, definitions); `mono` for code. */
  export let label: string | null = null;
  export let value = '';
  export let placeholder = '';
  export let type: 'text' | 'email' | 'number' = 'text';
  export let multiline = false;
  export let rows = 6;
  export let mono = false;
  export let hint: string | null = null;
  export let error: string | null = null;
  export let required = false;
  export let disabled = false;
  export let id: string | null = null;

  const fieldId = id ?? `tf-${Math.random().toString(36).slice(2, 9)}`;
</script>

<div class="flex flex-col gap-1.5">
  {#if label}
    <label for={fieldId} class="text-xs font-medium text-text-2">
      {label}{#if required}<span class="text-danger"> *</span>{/if}
    </label>
  {/if}
  {#if multiline}
    <textarea
      id={fieldId}
      {rows}
      {placeholder}
      {required}
      {disabled}
      class="field"
      class:field-mono={mono}
      class:field-error={error}
      bind:value
      on:input
    ></textarea>
  {:else}
    <input
      id={fieldId}
      {type}
      {placeholder}
      {required}
      {disabled}
      class="field"
      class:field-mono={mono}
      class:field-error={error}
      bind:value
      on:input
    />
  {/if}
  {#if error}
    <p class="text-xs text-danger">{error}</p>
  {:else if hint}
    <p class="text-xs text-text-3">{hint}</p>
  {/if}
</div>

<style>
  .field {
    width: 100%;
    background: var(--color-surface);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-md);
    padding: 0.5rem 0.7rem;
    font-size: 14px;
    color: var(--color-text-1);
    transition:
      border-color 0.14s ease-out,
      box-shadow 0.14s ease-out;
  }
  .field::placeholder {
    color: var(--color-text-3);
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
  .field-mono {
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 1.6;
  }
  .field-error {
    border-color: var(--color-danger);
  }
  .field-error:focus {
    box-shadow: 0 0 0 3px rgba(185, 28, 28, 0.1);
  }
  textarea.field {
    resize: vertical;
    min-height: 4rem;
  }
</style>

<!--
  Pill — the one rounded badge primitive. Every domain chip (grader grade,
  decision outcome, funnel label/failure-mode) renders through this so the
  shape, size, and tone→token mapping live in exactly one place. Tone drives
  colour via viz/tones.ts, which reads app.css vars → dark mode for free.
-->
<script lang="ts">
  import type { Tone } from '$lib/viz/tones';
  import { toneFg, toneBg } from '$lib/viz/tones';
  import type { Snippet } from 'svelte';

  let {
    tone = 'neutral',
    title,
    children
  }: {
    tone?: Tone;
    title?: string;
    children: Snippet;
  } = $props();
</script>

<span
  class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
  style:color={toneFg(tone)}
  style:background={toneBg(tone)}
  {title}
>
  {@render children()}
</span>

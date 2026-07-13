<!--
  AudioPlayer: play an audio clip stored behind an object-store pointer.

  Voice spans (stt/tts) reference their audio by `audio_pointer` + carry an
  `audio_mime_type`. The bytes live in the object store; we hit /payloads with
  the mime hint so the <audio> element gets a playable response. Lazy: builds
  the src URL only when present, and the browser streams on play.
-->
<script lang="ts">
  import { page } from '$app/stores';

  let {
    pointer,
    mimeType,
    label
  }: { pointer: string | null; mimeType: string | null; label: string } = $props();

  const src = $derived.by(() => {
    if (!pointer) return null;
    const ws = $page.params.workspace;
    if (!ws) return null;
    const params = new URLSearchParams({ pointer });
    if (mimeType) params.set('content_type', mimeType);
    return `/api/workspaces/${ws}/payloads?${params.toString()}`;
  });
</script>

{#if src}
  <div class="audio-field">
    <span class="audio-label">{label}</span>
    <!-- svelte-ignore a11y_media_has_caption -->
    <audio class="audio-el" controls preload="none" {src}></audio>
  </div>
{/if}

<style>
  .audio-field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .audio-label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--color-text-3);
  }
  .audio-el {
    width: 100%;
    height: 2.2rem;
  }
</style>

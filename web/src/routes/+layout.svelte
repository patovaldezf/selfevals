<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import Toaster from '$lib/components/Toaster.svelte';
  import CommandPalette from '$lib/components/ui/CommandPalette.svelte';
  import ShortcutHelp from '$lib/components/ui/ShortcutHelp.svelte';
  import { theme } from '$lib/stores/theme';

  onMount(() => theme.init());
</script>

<slot />
<Toaster />
<CommandPalette />
<ShortcutHelp />

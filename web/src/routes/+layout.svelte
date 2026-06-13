<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import Toaster from '$lib/components/Toaster.svelte';
  import { theme } from '$lib/stores/theme';

  onMount(() => theme.init());
</script>

<slot />
<Toaster />

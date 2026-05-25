<script lang="ts">
  import AppShell from '$lib/components/AppShell.svelte';
  import type { LayoutData } from './$types';

  export let data: LayoutData;
</script>

<AppShell workspaceId={data.workspace.id}>
  <slot />
</AppShell>

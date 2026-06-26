<script lang="ts">
  import Badge from './Badge.svelte';
  import { phaseChip } from '../status';

  interface Props {
    /** The promotion's `current_phase` (stored) or `live_status.phase` (live) — a plain string. */
    phase: string | null | undefined;
  }
  let { phase }: Props = $props();
  const chip = $derived(phaseChip(phase));
</script>

<Badge tone={chip.tone}>{chip.label}</Badge>

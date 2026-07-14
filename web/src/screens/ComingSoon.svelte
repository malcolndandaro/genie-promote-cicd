<script lang="ts">
  // S2: a placeholder for a nav section whose real screen hasn't landed yet (Aprovações de
  // acesso -> S5, Revisão de promoções -> S6). Keeps the persona-sectioned sidebar's nav items
  // resolving to something coherent instead of a dead route while those slices are pending.
  import Card from '../lib/components/Card.svelte';

  interface Props {
    title: string;
    description: string;
  }
  let { title, description }: Props = $props();
</script>

<Card {title}>
  <p class="muted text-sm">{description}</p>
</Card>

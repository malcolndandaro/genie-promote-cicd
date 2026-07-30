<script lang="ts">
  // One AI/BI dashboard in the list. Dashboard-native on purpose: no "espaço" wording, and the glyph
  // + env badge come from the kind registry rather than being hardcoded per screen.
  import Badge from './Badge.svelte';
  import Button from './Button.svelte';
  import { kindMeta } from '../resources';
  import type { PromotableResource } from '../types';

  interface Props {
    resource: PromotableResource;
    onPromote: (resource: PromotableResource) => void;
    busy?: boolean;
    disabled?: boolean;
    selected?: boolean;
  }
  let { resource, onPromote, busy = false, disabled = false, selected = false }: Props = $props();

  let meta = $derived(kindMeta(resource.kind));
</script>

<article class="painel-card" class:painel-card--selected={selected}>
  <div class="painel-card__head">
    <span class="painel-card__glyph" aria-hidden="true">{meta.glyph}</span>
    <div class="painel-card__titles">
      <h3 class="painel-card__title">{resource.title}</h3>
      <div class="painel-card__badges">
        <Badge tone="neutral">{meta.label}</Badge>
        {#if resource.env}
          <Badge tone={resource.env === 'prod' ? 'success' : 'neutral'}>{resource.env}</Badge>
        {/if}
      </div>
    </div>
  </div>
  <Button
    variant={selected ? 'outline' : 'primary'}
    onclick={() => onPromote(resource)}
    {disabled}
    ariaLabel={busy ? undefined : `Preparar promoção: ${resource.title}`}
  >
    {busy ? 'Preparando…' : selected ? 'Selecionado' : 'Preparar promoção →'}
  </Button>
</article>

<style>
  .painel-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .painel-card--selected {
    border-color: var(--primary);
    box-shadow: 0 0 0 1px var(--primary) inset;
  }
  .painel-card__head {
    display: flex;
    gap: var(--space-2);
    align-items: flex-start;
    min-width: 0;
  }
  .painel-card__glyph {
    font-size: 1.15rem;
    line-height: 1.4;
    color: var(--primary);
  }
  .painel-card__titles {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    min-width: 0;
  }
  .painel-card__title {
    margin: 0;
    font-size: 0.98rem;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .painel-card__badges {
    display: flex;
    gap: var(--space-1);
    flex-wrap: wrap;
  }
</style>

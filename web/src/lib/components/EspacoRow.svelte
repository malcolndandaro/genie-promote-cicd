<script lang="ts">
  // S3: one row of the merged "Meus espaços" page (D3/GR3) — a space's promote action (unchanged
  // SpaceCard) plus a collapsed status/count summary and an expandable per-attempt history
  // (reused PromotionList/PromotionCard, same as the old "Minhas promoções" list rendering).
  import SpaceCard from './SpaceCard.svelte';
  import PromotionList from './PromotionList.svelte';
  import Badge from './Badge.svelte';
  import { phaseChip } from '../status';
  import type { PromotableResource } from '../types';
  import type { PromotionSummary } from '../api';

  interface Props {
    resource: PromotableResource;
    /** This space's promotions, NEWEST FIRST. Empty = never promoted. */
    promotions: PromotionSummary[];
    expanded: boolean;
    onToggle: () => void;
    onOpenPromotion: (p: PromotionSummary) => void;
    onPromote: (resource: PromotableResource) => void;
    busy?: boolean;
    disabled?: boolean;
    selected?: boolean;
    /** When this Space owns the run currently shown on the right, its live/selected phase takes
     * precedence over the list snapshot so both sides can never contradict each other. */
    activePhase?: string | null;
    activeRequester?: string | null;
    activeTerminal?: boolean;
  }
  let {
    resource,
    promotions,
    expanded,
    onToggle,
    onOpenPromotion,
    onPromote,
    busy = false,
    disabled = false,
    selected = false,
    activePhase = null,
    activeRequester = null,
    activeTerminal = false,
  }: Props = $props();

  const latest = $derived(promotions[0] ?? null);
  const displayedPhase = $derived(activePhase ?? latest?.current_phase ?? null);
  const chip = $derived(displayedPhase ? phaseChip(displayedPhase) : null);
  // D7: an open (non-terminal) latest promotion IS the status shown here — so a second promoter
  // sees "who already has this in flight" before clicking anything, not just after.
  const displayedRequester = $derived(activePhase ? activeRequester : latest?.requester_email);
  const displayedTerminal = $derived(activePhase ? activeTerminal : (latest?.terminal ?? false));
  const showsRequester = $derived(!!displayedPhase && !displayedTerminal && !!displayedRequester);
</script>

<div class="espaco-row">
  <div class="espaco-row__summary">
    <button
      type="button"
      class="espaco-row__toggle"
      onclick={onToggle}
      aria-expanded={expanded}
      disabled={promotions.length === 0}
      aria-label={`${expanded ? 'Recolher' : 'Expandir'} histórico de ${resource.title}`}
    >
      <span class="espaco-row__chevron" class:espaco-row__chevron--open={expanded} aria-hidden="true">▸</span>
    </button>
    {#if chip}
      <Badge tone={chip.tone}>{chip.label}</Badge>
      {#if showsRequester}
        <span class="muted text-xs">— {displayedRequester}</span>
      {/if}
      <span class="muted text-xs">
        {promotions.length} {promotions.length === 1 ? 'promoção' : 'promoções'}
      </span>
    {:else}
      <span class="muted text-xs">Nunca promovido</span>
    {/if}
  </div>

  <SpaceCard
    {resource}
    {onPromote}
    onOpen={latest ? () => onOpenPromotion(latest) : undefined}
    {busy}
    {disabled}
    {selected}
  />

  {#if expanded && promotions.length > 0}
    <div class="espaco-row__history">
      <PromotionList {promotions} onOpen={onOpenPromotion} />
    </div>
  {/if}
</div>

<style>
  .espaco-row {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .espaco-row__summary {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
    padding: 0 var(--space-1);
  }
  .espaco-row__toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.5rem;
    height: 1.5rem;
    border: none;
    background: none;
    color: var(--muted-foreground);
    cursor: pointer;
    border-radius: var(--radius-sm);
  }
  .espaco-row__toggle:disabled {
    opacity: 0.35;
    cursor: default;
  }
  .espaco-row__toggle:not(:disabled):hover {
    background: var(--surface-inset);
  }
  .espaco-row__chevron {
    display: inline-block;
    font-size: 0.7rem;
    transition: transform 0.15s ease;
  }
  .espaco-row__chevron--open {
    transform: rotate(90deg);
  }
  .espaco-row__history {
    padding-left: var(--space-5);
  }
</style>

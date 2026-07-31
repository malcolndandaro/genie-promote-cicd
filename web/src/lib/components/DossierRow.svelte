<script lang="ts">
  // ONE entry in the dossier index — the shared record row for every promotable resource kind.
  //
  // Why a row and not a card: a card of icon + title + button reveals nothing until you click it, so
  // the old grid made the author open records to find out which ones needed attention. A dossier row
  // puts the standing ON the index: title, kind, who has it in flight, how many prior promotions,
  // and when it last moved — all readable in one vertical scan, with the action at the end.
  //
  // Kind-specific content arrives through the `facts` snippet (a Space passes its audience/benchmark
  // facts, a painel its datasets/widgets/pages) so the STRUCTURE is shared and only the evidence
  // differs. The row never hardcodes one kind's vocabulary.
  import type { Snippet } from 'svelte';
  import Badge from './Badge.svelte';
  import Button from './Button.svelte';
  import Icon from './Icon.svelte';
  import type { IconName } from './Icon.svelte';
  import { phaseChip } from '../status';
  import type { PromotableResource } from '../types';
  import type { PromotionSummary } from '../api';

  interface Props {
    resource: PromotableResource;
    /** Icon for this resource kind, drawn from the app's own set (never a glyph). */
    icon: IconName;
    /** Human label for the kind, e.g. "Genie Space" / "Painel AI/BI". */
    kindLabel: string;
    /** This resource's promotions, NEWEST FIRST. Empty = never promoted. */
    promotions: PromotionSummary[];
    /** Kind-specific evidence line (datasets/widgets/pages, or audience/benchmarks). */
    facts?: Snippet;
    /** Expanded history disclosure. */
    expanded: boolean;
    onToggle: () => void;
    history?: Snippet;
    /** Begin a promotion for this resource. */
    onPromote: (resource: PromotableResource) => void;
    /** Open this resource's most recent stored promotion. */
    onOpenLatest?: () => void;
    busy?: boolean;
    disabled?: boolean;
    selected?: boolean;
    /** The live phase when THIS resource owns the run currently on screen. */
    activePhase?: string | null;
    activeRequester?: string | null;
    activeTerminal?: boolean;
    activePhaseLoading?: boolean;
  }
  let {
    resource,
    icon,
    kindLabel,
    promotions,
    facts,
    expanded,
    onToggle,
    history,
    onPromote,
    onOpenLatest,
    busy = false,
    disabled = false,
    selected = false,
    activePhase = null,
    activeRequester = null,
    activeTerminal = false,
    activePhaseLoading = false,
  }: Props = $props();

  const latest = $derived(promotions[0] ?? null);
  const displayedPhase = $derived(activePhase ?? latest?.current_phase ?? null);
  const chip = $derived(displayedPhase ? phaseChip(displayedPhase) : null);
  // An open (non-terminal) promotion IS the standing shown here — so a second promoter sees who
  // already has this in flight BEFORE clicking anything, not after.
  const displayedRequester = $derived(activePhase ? activeRequester : latest?.requester_email);
  const displayedTerminal = $derived(activePhase ? activeTerminal : (latest?.terminal ?? false));
  const showsRequester = $derived(!!displayedPhase && !displayedTerminal && !!displayedRequester);
  const inFlight = $derived(!!displayedPhase && !displayedTerminal);

  /** Short, locale-correct "when it last moved" — the register's rightmost fact. */
  const lastMoved = $derived.by(() => {
    const iso = latest?.updated_at ?? latest?.created_at;
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: '2-digit' });
  });
</script>

<article
  class="dossier"
  class:dossier--selected={selected}
  class:dossier--flight={inFlight}
  aria-label={resource.title}
>
  <div class="dossier__main">
    <span class="dossier__glyph" aria-hidden="true"><Icon name={icon} size={16} /></span>

    <div class="dossier__ident">
      {#if onOpenLatest}
        <button
          type="button"
          class="dossier__title-btn"
          onclick={onOpenLatest}
          aria-label={`Abrir promoção mais recente: ${resource.title}`}
        >
          <h3 class="dossier__title">{resource.title}</h3>
        </button>
      {:else}
        <h3 class="dossier__title dossier__title--static">{resource.title}</h3>
      {/if}

      <!-- The kind is NOT repeated per row: the register is single-kind and its column header
           already declares it, so restating it on every entry is noise the eye has to skip.
           Separators are drawn by CSS between siblings, so no combination of present/absent facts
           can produce a leading, trailing or doubled dot. -->
      <p class="dossier__meta tnum">
        {#if resource.env}<span>{resource.env === 'prod' ? 'prod' : 'dev'}</span>{/if}
        {#if promotions.length > 0}
          <span>{promotions.length} {promotions.length === 1 ? 'promoção' : 'promoções'}</span>
        {/if}
        {#if lastMoved}<span>{lastMoved}</span>{/if}
      </p>

      {#if facts}<div class="dossier__facts">{@render facts()}</div>{/if}
    </div>

    <div class="dossier__standing">
      {#if activePhaseLoading}
        <span class="dossier__loading" role="status">
          <span class="dossier__spinner" aria-hidden="true"></span>
          Atualizando status…
        </span>
      {:else if chip}
        <Badge tone={chip.tone}>{chip.label}</Badge>
        {#if showsRequester}
          <span class="dossier__requester" title={displayedRequester ?? undefined}>
            — {displayedRequester}
          </span>
        {/if}
      {:else}
        <span class="dossier__never">Nunca promovido</span>
      {/if}
    </div>

    <div class="dossier__action">
      <Button
        onclick={() => onPromote(resource)}
        {disabled}
        loading={busy}
        ariaLabel={busy ? undefined : `Preparar promoção: ${resource.title}`}
      >
        {busy ? 'Preparando…' : 'Preparar promoção'}
      </Button>
    </div>
  </div>

  {#if promotions.length > 0 && history}
    <div class="dossier__disclosure">
      <button
        type="button"
        class="dossier__toggle"
        onclick={onToggle}
        aria-expanded={expanded}
        aria-label={`${expanded ? 'Recolher' : 'Expandir'} histórico de ${resource.title}`}
      >
        <span class="dossier__chevron" class:dossier__chevron--open={expanded} aria-hidden="true">
          <Icon name="chevron-right" size={13} />
        </span>
        Histórico
      </button>
      {#if expanded}
        <div class="dossier__history">{@render history()}</div>
      {/if}
    </div>
  {/if}
</article>

<style>
  /* Register entries share edges: the index reads as one continuous ledger, not a scatter of
     floating cards. The container supplies the outer frame; a row owns only its bottom rule. */
  .dossier {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    transition: background-color 0.12s ease;
  }
  .dossier:last-child {
    border-bottom: 0;
  }
  .dossier:hover {
    background: var(--surface-inset);
  }
  /* Selection is a left marker plus a tint — no lift, no resize, so the index never reflows. */
  .dossier--selected {
    background: var(--accent-soft);
    box-shadow: inset 2px 0 0 var(--accent);
  }
  .dossier--selected:hover {
    background: var(--accent-soft);
  }
  /* An in-flight record is marked on the index itself, so "what needs attention" survives a scan. */
  .dossier--flight:not(.dossier--selected) {
    box-shadow: inset 2px 0 0 var(--warning);
  }

  .dossier__main {
    display: grid;
    /* Every track is allowed to give: the identity column keeps a floor so a title never wraps one
       character per line, the standing track shrinks from a comfortable 13rem rather than being
       fixed (a fixed track plus a nowrap button overflowed the register's clip edge when the
       working record narrowed this column), and the action track reserves exactly the button. */
    grid-template-columns: auto minmax(7rem, 1fr) minmax(6rem, 13rem) auto;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
  }
  .dossier__glyph {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    color: var(--muted-foreground);
  }
  .dossier__ident {
    min-width: 0;
  }
  /* `inline-block`, not a full-width block: a 100%-wide button would extend under the standing
     column, where the phase badge then intercepts its clicks. The hit area is the title itself. */
  .dossier__title-btn {
    display: inline-block;
    max-width: 100%;
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .dossier__title-btn:focus-visible {
    outline: 2px solid var(--ring);
    outline-offset: 2px;
    border-radius: var(--radius-sm);
  }
  .dossier__title {
    font-size: 0.9375rem;
    font-weight: 600;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
  .dossier__title-btn:hover .dossier__title {
    color: var(--accent-hover);
  }
  .dossier__title--static {
    cursor: default;
  }
  .dossier__meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.3rem;
    margin: 0.15rem 0 0;
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  /* CSS owns the separators, so any subset of the facts renders without a stray dot. */
  .dossier__meta > span + span::before {
    content: '·';
    margin-right: 0.3rem;
    opacity: 0.6;
  }
  .dossier__facts {
    margin-top: 0.3rem;
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  /* The standing column gets a real track of its own (`minmax` in the grid above), and its contents
     stay INSIDE it: a phase label like "Aguardando aprovação da Plataforma" is wide, and without an
     explicit floor the auto track collapsed while the badge overflowed leftward across the title —
     silently stealing the title button's clicks. Wrapping keeps the requester readable rather than
     clipped, since it answers "who already has this in flight". */
  .dossier__standing {
    display: flex;
    align-items: flex-end;
    flex-direction: column;
    gap: var(--space-1);
    min-width: 0;
    text-align: right;
  }
  .dossier__standing > :global(.badge) {
    max-width: 100%;
    /* A long phase label wraps within the badge instead of forcing the track wider. Tighter leading
       than body text so a two-line phase stays a compact block rather than stretching the row. */
    white-space: normal;
    line-height: 1.25;
    text-align: right;
  }
  .dossier__requester {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  .dossier__never {
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  .dossier__loading {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--muted-foreground);
    font-size: 0.75rem;
  }
  .dossier__spinner {
    width: 0.75rem;
    height: 0.75rem;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: dossier-spin 0.7s linear infinite;
  }
  @keyframes dossier-spin {
    to {
      transform: rotate(360deg);
    }
  }
  .dossier__action {
    flex-shrink: 0;
  }
  /* Deliberately NOT `white-space: nowrap`: the register clips its overflow, so forcing one line
     cut the button's right edge off instead of wrapping it. A two-line label is legible; a
     half-visible one is not. `hyphens` keeps the wrap from splitting mid-word awkwardly. */
  .dossier__action :global(.btn) {
    text-align: center;
  }

  .dossier__disclosure {
    padding: 0 var(--space-4) var(--space-3);
  }
  .dossier__toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.2rem 0;
    border: 0;
    background: none;
    color: var(--muted-foreground);
    font: inherit;
    font-size: 0.75rem;
    cursor: pointer;
  }
  .dossier__toggle:hover {
    color: var(--foreground);
  }
  .dossier__chevron {
    display: inline-flex;
    transition: transform 0.15s ease;
  }
  .dossier__chevron--open {
    transform: rotate(90deg);
  }
  .dossier__history {
    margin-top: var(--space-2);
  }

  /* Narrow: the register becomes stacked entries — standing and action drop below the identity
     rather than compressing into unreadable columns. */
  @media (max-width: 860px) {
    .dossier__main {
      grid-template-columns: auto minmax(0, 1fr);
      row-gap: var(--space-2);
    }
    .dossier__standing {
      grid-column: 2;
      justify-content: flex-start;
    }
    .dossier__action {
      grid-column: 2;
    }
  }
</style>

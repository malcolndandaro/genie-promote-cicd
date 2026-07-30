<script lang="ts">
  // The dashboard-native confirm step: what IS this painel, where will it be filed, who can open it.
  //
  // Three declarations, numbered so the author knows how many decisions they own:
  //   1. the production name (pre-filled with the dev title, editable) + the table de-para;
  //   2. the audience (derives CAN_READ, the dashboard level);
  //   3. the business area — which is also the repository path.
  //
  // The structural summary above them is read from the REAL definition, so the author confirms they
  // picked the right painel before promoting rather than after reading a PR diff.
  import Button from './Button.svelte';
  import Card from './Card.svelte';
  import PromotionMappingForm from './PromotionMappingForm.svelte';
  import AudienceSpecForm from './AudienceSpecForm.svelte';
  import AreaPicker from './AreaPicker.svelte';
  import { getPromotePreview } from '../api';
  import { resourceName } from '../resource_name';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    onCancel: () => void;
  }
  let { promotion, onCancel }: Props = $props();

  interface Structure {
    datasets: string[];
    n_widgets: number;
    pages: string[];
  }
  let structure = $state<Structure | null>(null);
  let structureError = $state<string | null>(null);
  let loadedForId: string | null = null;

  // Load the painel's shape for the summary. Best-effort and additive: a failure must never block a
  // promotion that would otherwise work, so it degrades to a quiet note.
  $effect(() => {
    const resource = promotion.resource;
    if (!resource || loadedForId === resource.id) return;
    loadedForId = resource.id;
    structure = null;
    structureError = null;
    getPromotePreview(resource.id, 'dashboard')
      .then((preview) => {
        if (loadedForId !== resource.id) return;
        structure = (preview as { structure?: Structure }).structure ?? null;
      })
      .catch((e: unknown) => {
        if (loadedForId !== resource.id) return;
        structureError = e instanceof Error ? e.message : String(e);
      });
  });

  // The name the promotion will use inside the area, derived from whatever title is declared right
  // now (the mapping form publishes it as `pendingProdTitle`).
  let derivedName = $derived(
    resourceName(promotion.pendingProdTitle || promotion.resource?.title || ''),
  );

  let canConfirm = $derived(!!promotion.pendingAudienceSpec && !!promotion.pendingArea);
</script>

<Card>
  <div class="confirm">
    <header class="confirm__head">
      <h2>Confirme a promoção deste painel</h2>
      <p class="muted text-sm">
        O app abre um <strong>rascunho</strong> de PR com a revisão automática. O Responsável Técnico
        revisa e promove no GitHub; a Plataforma aprova o deploy.
      </p>
    </header>

    <section class="confirm__structure" aria-label="Estrutura do painel">
      {#if structure}
        <div class="structure__grid">
          <div class="structure__stat">
            <strong>{structure.datasets.length}</strong>
            <span>{structure.datasets.length === 1 ? 'dataset' : 'datasets'}</span>
          </div>
          <div class="structure__stat">
            <strong>{structure.n_widgets}</strong>
            <span>{structure.n_widgets === 1 ? 'widget' : 'widgets'}</span>
          </div>
          <div class="structure__stat">
            <strong>{structure.pages.length}</strong>
            <span>{structure.pages.length === 1 ? 'página' : 'páginas'}</span>
          </div>
        </div>
        {#if structure.datasets.length > 0}
          <p class="structure__list">
            <span class="muted text-xs">Datasets</span>
            <span>{structure.datasets.join(' · ')}</span>
          </p>
        {/if}
      {:else if structureError}
        <p class="text-xs muted">
          Não foi possível ler a estrutura do painel ({structureError}) — a promoção segue normalmente e
          as checagens estruturais rodam na revisão.
        </p>
      {:else}
        <p class="text-sm muted" role="status" aria-busy="true">Lendo a estrutura do painel…</p>
      {/if}
    </section>

    <section class="confirm__section">
      <PromotionMappingForm {promotion} />
    </section>

    <section class="confirm__section">
      <AudienceSpecForm {promotion} />
    </section>

    <section class="confirm__section">
      <AreaPicker {promotion} {derivedName} />
    </section>

    <footer class="confirm__actions">
      <Button variant="outline" onclick={onCancel}>← Escolher outro painel</Button>
      <Button
        onclick={() => promotion.requestPromotion()}
        disabled={!canConfirm}
        ariaLabel="Confirmar promoção — revisar e abrir o rascunho"
      >
        Revisar e solicitar promoção →
      </Button>
    </footer>
    {#if !canConfirm}
      <p class="muted text-xs" role="note">
        Para continuar: escolha o público e a área de negócio.
      </p>
    {/if}
  </div>
</Card>

<style>
  .confirm {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .confirm__head h2 {
    margin: 0 0 var(--space-1);
    font-size: 1.1rem;
  }
  .confirm__head p {
    margin: 0;
  }
  .confirm__structure {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-3);
    border-radius: var(--radius);
    background: var(--surface-inset);
  }
  .structure__grid {
    display: flex;
    gap: var(--space-4);
    flex-wrap: wrap;
  }
  .structure__stat {
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .structure__stat strong {
    font-size: 1.3rem;
  }
  .structure__stat span {
    font-size: 0.78rem;
    color: var(--muted-foreground);
  }
  .structure__list {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    margin: 0;
    font-size: 0.82rem;
    overflow-wrap: anywhere;
  }
  .confirm__section {
    min-width: 0;
  }
  .confirm__actions {
    display: flex;
    gap: var(--space-2);
    flex-wrap: wrap;
    justify-content: space-between;
  }
</style>

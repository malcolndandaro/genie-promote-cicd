<script lang="ts">
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import Select from '../lib/components/Select.svelte';
  import { getResources, isAuthError } from '../lib/api';
  import { kindMeta } from '../lib/resources';
  import type { Promotion } from '../lib/promotion.svelte';
  import type { PromotableResource } from '../lib/types';

  interface Props {
    promotion: Promotion;
    onGoToNew?: () => void;
  }
  let { promotion, onGoToNew }: Props = $props();

  // The user's promotable resources (OBO). In $state so an error is retryable.
  let resourcesP = $state(getResources());

  // `promotion` is the single source of truth for the selection — no parallel local state.
  const reload = () => {
    resourcesP = getResources();
    promotion.select(null); // a fresh list invalidates any prior (possibly gone) selection
  };

  function onSelect(id: string, resources: PromotableResource[]) {
    promotion.select(resources.find((r) => r.id === id) ?? null);
  }
</script>

<Card title="Meus espaços" subtitle="Selecione um recurso e solicite a revisão de promoção.">
  {#await resourcesP}
    <div class="stack">
      <Skeleton height="2.6rem" />
      <Skeleton height="2.6rem" width="60%" />
    </div>
  {:then resources}
    {#if resources.length === 0}
      <div class="empty">
        <p class="empty__title">Nenhum Genie Space encontrado</p>
        <p class="muted text-sm">
          Crie um para começar a promover — a autoria rica acontece no Genie nativo.
        </p>
        {#if onGoToNew}
          <Button variant="outline" onclick={onGoToNew}>＋ Novo Genie Space</Button>
        {/if}
      </div>
    {:else}
      <div class="stack">
        <div class="field">
          <label class="field__label" for="meus-espacos-select">Recurso</label>
          <Select
            id="meus-espacos-select"
            options={resources.map((r) => ({ value: r.id, label: r.title }))}
            value={promotion.selectedId}
            onchange={(id) => onSelect(id, resources)}
            placeholder="Selecione um espaço"
            disabled={promotion.phase === 'reviewing'}
          />
        </div>

        {#if promotion.resource}
          <p class="selected-note text-sm">
            <Badge tone="accent">{kindMeta(promotion.resource.kind).label}</Badge>
            <span>{promotion.resource.title}</span>
          </p>
        {/if}

        <div>
          <Button
            onclick={() => promotion.requestReview()}
            disabled={!promotion.resource}
            loading={promotion.phase === 'reviewing'}
          >
            {promotion.phase === 'reviewing' ? 'Revisando…' : 'Solicitar promoção →'}
          </Button>
        </div>
      </div>
    {/if}
  {:catch err}
    <div class="error-state" role="alert">
      <span class="error">
        {#if isAuthError(err)}
          Sessão expirada — recarregue a página para reautenticar.
        {:else}
          Não foi possível listar os espaços: {err instanceof Error ? err.message : String(err)}
        {/if}
      </span>
      {#if isAuthError(err)}
        <Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
      {:else}
        <Button variant="outline" onclick={reload}>Tentar novamente</Button>
      {/if}
    </div>
  {/await}

  <!-- The review result. SV3 replaces this minimal summary with the full animated pipeline panel. -->
  {#if promotion.phase === 'error'}
    <div class="review-stub error-state" role="alert">
      <span class="error">Não foi possível revisar: {promotion.error}</span>
      <Button variant="outline" onclick={() => promotion.requestReview()}>Tentar novamente</Button>
    </div>
  {:else if promotion.phase === 'reviewed' && promotion.review}
    <div class="review-stub">
      <p class="text-sm">
        <Badge tone={promotion.review.gate.conclusion === 'failure' ? 'destructive' : 'success'}>
          {promotion.review.gate.conclusion === 'failure' ? 'Bloqueado' : 'OK'}
        </Badge>
        {promotion.review.gate.summary}
      </p>
      <p class="muted text-xs">{promotion.review.findings.length} achado(s) — detalhamento na próxima etapa.</p>
    </div>
  {/if}
</Card>

<style>
  .field {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .field__label {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted-foreground);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .selected-note {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin: 0;
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-4) 0;
  }
  .empty__title {
    margin: 0;
    font-weight: 600;
  }
  .error-state {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
  .review-stub {
    margin-top: var(--space-5);
    padding-top: var(--space-4);
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
</style>

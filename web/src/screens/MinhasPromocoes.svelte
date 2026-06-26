<script lang="ts">
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import { getPromotions, ApiError, type PromotionSummary, type PromotePhase } from '../lib/api';
  import { kindMeta } from '../lib/resources';
  import { PHASE_LABEL, phaseTone } from '../lib/status';
  import type { Whoami, ResourceKind } from '../lib/types';

  interface Props {
    who: Whoami | null;
    /** Open a promotion (loads its stored snapshot + audit into the review view, no reviewer re-run). */
    onOpen: (summary: PromotionSummary) => void;
  }
  let { who, onOpen }: Props = $props();

  // Admins/Stewards may view ALL promotions (server-enforced); everyone else only their own.
  let scope = $state<'mine' | 'all'>('mine');
  let promotionsP = $state(getPromotions('mine'));

  function load(s: 'mine' | 'all') {
    scope = s;
    promotionsP = getPromotions(s);
  }

  function fmt(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }

  // The phase string from the store maps to the live-phase label set; fall back to the raw value.
  const phaseLabel = (p: string | null) => (p && PHASE_LABEL[p as PromotePhase]) || p || '—';
</script>

<Card title="Minhas promoções" subtitle="Histórico de promoções — abra uma para ver a revisão e a trilha de auditoria.">
  {#if who?.is_admin}
    <div class="scope" role="group" aria-label="Escopo">
      <Button variant={scope === 'mine' ? 'primary' : 'outline'} onclick={() => load('mine')}>Minhas</Button>
      <Button variant={scope === 'all' ? 'primary' : 'outline'} onclick={() => load('all')}>Todas (Steward/Admin)</Button>
    </div>
  {/if}

  {#await promotionsP}
    <div class="stack">
      <Skeleton height="3.2rem" />
      <Skeleton height="3.2rem" width="80%" />
    </div>
  {:then promotions}
    {#if promotions.length === 0}
      <div class="empty">
        <p class="empty__title">Nenhuma promoção ainda</p>
        <p class="muted text-sm">Solicite uma promoção em “Meus espaços” para começar o histórico.</p>
      </div>
    {:else}
      <ul class="rows">
        {#each promotions as p (p.id)}
          <li class="row">
            <div class="row__main">
              <div class="row__title">
                <Badge tone="accent">{kindMeta(p.resource_kind as ResourceKind).label}</Badge>
                <span class="row__name">{p.resource_title ?? p.resource_id}</span>
              </div>
              <div class="row__meta">
                <Badge tone={phaseTone((p.current_phase ?? 'open') as PromotePhase)}>
                  {phaseLabel(p.current_phase)}
                </Badge>
                {#if p.pr_number}
                  <a class="row__pr" href={p.pr_url ?? '#'} target="_blank" rel="noopener noreferrer">
                    PR #{p.pr_number} ↗
                  </a>
                {/if}
                <time class="muted text-xs">{fmt(p.updated_at)}</time>
              </div>
            </div>
            <Button variant="outline" onclick={() => onOpen(p)}>Abrir →</Button>
          </li>
        {/each}
      </ul>
    {/if}
  {:catch err}
    <div class="error-state" role="alert">
      <span class="error">
        {#if err instanceof ApiError && err.status === 401}
          Sessão expirada — recarregue para reautenticar.
        {:else if err instanceof ApiError && err.status === 403}
          Sem permissão para ver todas as promoções — contacte o administrador.
        {:else}
          Não foi possível carregar as promoções: {err instanceof Error ? err.message : String(err)}
        {/if}
      </span>
      <Button variant="outline" onclick={() => load(scope)}>Tentar novamente</Button>
    </div>
  {/await}
</Card>

<style>
  .scope {
    display: flex;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
  }
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
  }
  .row__main {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    min-width: 0;
  }
  .row__title {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .row__name {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row__meta {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .row__pr {
    font-weight: 600;
    color: var(--accent-hover);
    white-space: nowrap;
  }
  .empty {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
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
</style>

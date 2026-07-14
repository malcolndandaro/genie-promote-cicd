<script lang="ts">
  // S6 (app-ux-overhaul, D2): the Steward's own monitoring dashboard — a read-only, cross-space
  // view of promotions currently awaiting THEIR attention (PR review/merge, or the prod deploy
  // gate), each linking straight out to GitHub. No new in-app approval action — the Steward's
  // real approval stays on GitHub (ADR-0002's separation-of-duties design; "reflect, never
  // assert", same principle CONTEXT.md already applies to Promotion Phase).
  import Card from '../lib/components/Card.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import { getPromotions, isAuthError } from '../lib/api';
  import StatusChip from '../lib/components/StatusChip.svelte';

  let promotionsP = $state(getPromotions('steward-queue'));

  function reload(): void {
    promotionsP = getPromotions('steward-queue');
  }

  function when(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }
</script>

<div class="stack">
  <Card
    title="Aguardando minha revisão"
    subtitle="Promoções, em todos os espaços, aguardando revisão do PR ou o gate de deploy em produção. A aprovação em si acontece no GitHub."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={reload}>Atualizar</button>
    {/snippet}
    {#await promotionsP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then promotions}
      {#if promotions.length === 0}
        <p class="muted text-sm">Nenhuma promoção aguardando revisão no momento.</p>
      {:else}
        <ul class="row-list">
          {#each promotions as p (p.id)}
            <li class="row">
              <div class="row__main">
                <strong>{p.resource_title ?? p.resource_id}</strong>
                <span class="muted text-xs">solicitado por {p.requester_email ?? '—'}</span>
              </div>
              <div class="row__meta">
                <StatusChip phase={p.current_phase} />
                <time class="muted text-xs">{when(p.updated_at)}</time>
                {#if p.pr_url}
                  <a href={p.pr_url} target="_blank" rel="noopener noreferrer" class="pr-link">
                    Ver no GitHub ↗
                  </a>
                {:else if p.pr_number}
                  <span class="muted text-xs">PR #{p.pr_number}</span>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {:catch err}
      <p class="error" role="alert">
        {isAuthError(err) ? 'Sessão expirada — recarregue para reautenticar.' : `Erro: ${err instanceof Error ? err.message : String(err)}`}
      </p>
    {/await}
  </Card>
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .refresh {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--surface);
    color: inherit;
    border-radius: var(--radius-sm);
    padding: 0.35rem 0.75rem;
    font: inherit;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
  }
  .refresh:hover {
    background: var(--surface-inset);
  }
  .row-list {
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
    flex-wrap: wrap;
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .row__main {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .row__meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .pr-link {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--accent-hover);
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

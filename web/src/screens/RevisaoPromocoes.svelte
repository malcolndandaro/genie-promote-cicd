<script lang="ts">
  // R2 (roles-github-native): repurposed from the old Steward-gated monitoring dashboard into a
  // UNIVERSAL read-only panel — "Aguardando ação no GitHub". Visible to every authenticated user
  // (no persona gate), cross-user, read-only list of promotions currently awaiting merge or the
  // prod deploy gate, each linking straight to GitHub.
  //
  // "Reflect, never assert" stays: the panel shows GitHub state, never decides whether something
  // should be approved. The Technical Owner's actual approval/merge remains on GitHub; the Platform
  // team approves the deploy gate there too. This panel is just a convenience mirror.
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
    title="Aguardando ação no GitHub"
    subtitle="Promoções, em todos os espaços, aguardando merge pelo Responsável Técnico ou aprovação do gate de deploy pela Plataforma. A ação em si acontece no GitHub."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={reload}>Atualizar</button>
    {/snippet}
    {#await promotionsP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then promotions}
      {#if promotions.length === 0}
        <p class="muted text-sm">Nenhuma promoção aguardando ação no momento.</p>
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
                {#if p.external_url}
                  <a href={p.external_url} target="_blank" rel="noopener noreferrer" class="pr-link">
                    Ver no GitHub ↗
                  </a>
                {:else if p.external_id}
                  <span class="muted text-xs">Mudança {p.external_id}</span>
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

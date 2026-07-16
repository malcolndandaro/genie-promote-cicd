<script lang="ts">
  // S4 (app-ux-overhaul, GR4): the standalone, Platform-Admin-only Audit page — pulled OUT of the
  // general Admin console (D4), with combinable space/actor filters and a real date range +
  // pagination (the old `GET /admin/audit` only had a hard `limit`). Everyone else keeps today's
  // per-promotion audit trail (AuditTrail.svelte, embedded in PromotionReview) unchanged.
  import Card from '../lib/components/Card.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import { ApiError, getAdminAudit, getAdminRehydrateEvents, isAuthError, type AdminAuditQuery } from '../lib/api';
  import { genieSpaceUrl } from '../lib/links';
  import { EVENT_LABEL, actorDisplay } from '../lib/status';
  import type { AdminAuditRow } from '../lib/types';

  let { devHost = null }: { devHost?: string | null } = $props();

  const PAGE_SIZE = 50;

  let resourceId = $state('');
  let actor = $state('');
  let after = $state(''); // <input type=date> value, YYYY-MM-DD
  let before = $state('');
  let offset = $state(0);

  function buildQuery(): AdminAuditQuery {
    return {
      limit: PAGE_SIZE,
      offset,
      resourceId: resourceId.trim() || undefined,
      actor: actor.trim() || undefined,
      // A bare date is start-of-day for `after`, end-of-day for `before`, so the range is inclusive
      // of the whole selected day rather than an instant at midnight.
      after: after ? `${after}T00:00:00` : undefined,
      before: before ? `${before}T23:59:59.999` : undefined,
    };
  }

  let rowsP = $state(getAdminAudit(buildQuery()));
  let rehydrateP = $state(getAdminRehydrateEvents());
  // `hasMore` is a heuristic (a full page came back), not a real total count — no COUNT query
  // needed for an admin diagnostic tool at this app's scale.
  let hasMore = $state(false);

  function reload(): void {
    rowsP = getAdminAudit(buildQuery()).then((rows) => {
      hasMore = rows.length === PAGE_SIZE;
      return rows;
    });
  }
  reload();

  function applyFilters(): void {
    offset = 0;
    reload();
  }

  function clearFilters(): void {
    resourceId = '';
    actor = '';
    after = '';
    before = '';
    offset = 0;
    reload();
  }

  function nextPage(): void {
    offset += PAGE_SIZE;
    reload();
  }

  function prevPage(): void {
    offset = Math.max(0, offset - PAGE_SIZE);
    reload();
  }

  function when(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }

  const MODE_LABEL: Record<string, string> = { create: 'Criado', overwrite: 'Sobrescrito' };

  function errorText(err: unknown): string {
    if (isAuthError(err)) {
      return err instanceof ApiError && err.status === 403
        ? 'Sem permissão — esta tela é restrita a Admins.'
        : 'Sessão expirada — recarregue para reautenticar.';
    }
    return `Erro: ${err instanceof Error ? err.message : String(err)}`;
  }
</script>

<div class="stack">
  <Card
    title="Auditoria"
    subtitle="Quem mudou o quê, quando — todas as promoções, em todos os espaços e usuários, mais recentes primeiro."
  >
    <form class="filters" onsubmit={(e) => { e.preventDefault(); applyFilters(); }}>
      <label class="field">
        <span class="field__label">Espaço (resource id)</span>
        <input class="field__input" type="text" bind:value={resourceId} placeholder="ex.: s_01f171..." />
      </label>
      <label class="field">
        <span class="field__label">Usuário (login GitHub ou e-mail)</span>
        <input class="field__input" type="text" bind:value={actor} placeholder="ex.: pedro176 ou ana@x" />
      </label>
      <label class="field">
        <span class="field__label">De</span>
        <input class="field__input" type="date" bind:value={after} />
      </label>
      <label class="field">
        <span class="field__label">Até</span>
        <input class="field__input" type="date" bind:value={before} />
      </label>
      <div class="filters__actions">
        <Button type="submit">Filtrar</Button>
        <Button variant="outline" onclick={clearFilters}>Limpar</Button>
      </div>
    </form>

    {#await rowsP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then rows}
      {#if rows.length === 0}
        <p class="muted text-sm">Nenhum evento de auditoria corresponde aos filtros.</p>
      {:else}
        <ol class="row-list">
          {#each rows as e (e.seq + e.promotion_id)}
            {@const a = actorDisplay(e)}
            <li class="row">
              <div class="row__main">
                <strong>{EVENT_LABEL[e.event_type] ?? e.event_type}</strong>
                <span class="muted text-xs">{e.resource_title ?? e.resource_id}</span>
              </div>
              <div class="row__meta">
                <Badge tone={a.kind === 'github' ? 'accent' : 'neutral'}>{a.who}</Badge>
                {#if a.kind === 'app'}<span class="muted text-xs">(somente exibição)</span>{/if}
                <time class="muted text-xs">{when(e.github_event_at ?? e.occurred_at)}</time>
              </div>
            </li>
          {/each}
        </ol>
      {/if}
      <!-- Pagination stays visible even on an empty page (e.g. paged one too far past the real
           end) — otherwise "Próxima" would strand the caller with no way back to "Anterior". -->
      {#if offset > 0 || hasMore}
        <div class="pagination">
          <Button variant="outline" disabled={offset === 0} onclick={prevPage}>← Anterior</Button>
          {#if rows.length > 0}
            <span class="muted text-xs">{offset + 1}–{offset + rows.length}</span>
          {/if}
          <Button variant="outline" disabled={!hasMore} onclick={nextPage}>Próxima →</Button>
        </div>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card
    title="Exportações para dev"
    subtitle="Todo Space prod→dev exportado por este app, mais recente primeiro."
  >
    {#await rehydrateP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="70%" />
    {:then rows}
      {#if rows.length === 0}
        <p class="muted text-sm">Nenhuma exportação para dev ainda.</p>
      {:else}
        <ul class="row-list">
          {#each rows as e (e.id)}
            <li class="row">
              <div class="row__main">
                <strong>{e.resource_title ?? e.resource_id}</strong>
                <span class="muted text-xs">exportado por {e.actor_email}</span>
              </div>
              <div class="row__meta">
                <Badge tone={e.mode === 'overwrite' ? 'warning' : 'neutral'}>{MODE_LABEL[e.mode] ?? e.mode}</Badge>
                {#if e.dev_space_id && devHost}
                  <a class="dev-link" href={genieSpaceUrl(devHost, e.dev_space_id)} target="_blank" rel="noopener noreferrer">
                    Abrir no dev ↗
                  </a>
                {:else if e.dev_space_id}
                  <span class="muted text-xs">{e.dev_space_id}</span>
                {/if}
                <time class="muted text-xs">{when(e.created_at)}</time>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: var(--space-3);
    margin-bottom: var(--space-5);
    padding-bottom: var(--space-4);
    border-bottom: 1px solid var(--border);
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    min-width: 10rem;
  }
  .field__label {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .field__input {
    padding: 0.45rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font: inherit;
    background: var(--surface);
    color: inherit;
  }
  .filters__actions {
    display: flex;
    gap: var(--space-2);
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
  .dev-link {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--accent-hover);
  }
  .pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    margin-top: var(--space-4);
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

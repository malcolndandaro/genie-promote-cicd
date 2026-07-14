<script lang="ts">
  // F4 — admin console: a live prod inventory, the access-request queue (all states), and a
  // cross-Promotion audit view ("who changed what, when"). Every call here hits a server endpoint
  // gated on the A2-hardened VERIFIED identity (never the display-only x-forwarded-email header) —
  // this screen is only ever RENDERED for who?.is_admin (App.svelte), but the real gate is server-
  // side: a non-admin hitting these endpoints directly still gets a 403, surfaced below as an error.
  import Card from '../lib/components/Card.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import StatusChip from '../lib/components/StatusChip.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import {
    getAdminInventory, getAdminAudit, getAdminAccessRequests, getAdminRehydrateEvents,
    ApiError, isAuthError,
  } from '../lib/api';
  import { EVENT_LABEL } from '../lib/status';
  import { genieSpaceUrl } from '../lib/links';
  import type { AccessRequestState } from '../lib/types';

  /** The dev workspace host (`/api/whoami.dev_host`) — threaded down so "Abrir no dev" can link
   * straight to the exported Space when the dev workspace is configured. */
  let { devHost = null }: { devHost?: string | null } = $props();

  let inventoryP = $state(getAdminInventory());
  let accessP = $state(getAdminAccessRequests());
  let auditP = $state(getAdminAudit());
  let rehydrateP = $state(getAdminRehydrateEvents());

  function refresh(): void {
    inventoryP = getAdminInventory();
    accessP = getAdminAccessRequests();
    auditP = getAdminAudit();
    rehydrateP = getAdminRehydrateEvents();
  }

  const MODE_LABEL: Record<string, string> = { create: 'Criado', overwrite: 'Sobrescrito' };

  function errorText(err: unknown): string {
    if (isAuthError(err)) {
      return err instanceof ApiError && err.status === 403
        ? 'Sem permissão — este console é restrito a Stewards/Admins.'
        : 'Sessão expirada — recarregue para reautenticar.';
    }
    return `Erro: ${err instanceof Error ? err.message : String(err)}`;
  }

  const ACCESS_STATE_TONE: Record<AccessRequestState, 'neutral' | 'success' | 'warning' | 'destructive'> = {
    requested: 'warning',
    approved: 'destructive',
    applied: 'success',
    denied: 'destructive',
  };
  const ACCESS_STATE_LABEL: Record<AccessRequestState, string> = {
    requested: 'Aguardando aprovação',
    approved: 'Aprovado — falha ao aplicar',
    applied: 'Aplicado',
    denied: 'Negado',
  };

  function accessSummary(r: { want_space_permission: boolean; space_permission_level: string; want_uc_select: boolean }): string {
    const parts: string[] = [];
    if (r.want_space_permission) parts.push(`Espaço (${r.space_permission_level})`);
    if (r.want_uc_select) parts.push('Dados (UC SELECT)');
    return parts.join(' + ') || '—';
  }

  function accessSpecSummary(spec: { space_permissions: unknown[]; uc_principals: unknown[] } | null): string {
    if (!spec) return '—';
    const n = spec.space_permissions.length + spec.uc_principals.length;
    return n === 0 ? '—' : `${n} principal(is) declarado(s)`;
  }

  function when(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }
</script>

<div class="stack">
  <Card
    title="Inventário de produção"
    subtitle="Todo Genie Space implantado em produção — dono, acesso declarado e fase atual, lido ao vivo (nunca cache)."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={refresh}>Atualizar</button>
    {/snippet}
    {#await inventoryP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then inv}
      {#if inv.spaces.length === 0}
        <p class="muted text-sm">Nenhum Space encontrado em produção.</p>
      {:else}
        <ul class="row-list">
          {#each inv.spaces as s (s.space_id)}
            <li class="row">
              <div class="row__main">
                <strong>{s.title}</strong>
                <span class="muted text-xs">{s.space_id}</span>
              </div>
              <div class="row__meta">
                <span class="muted text-xs">Dono: {s.owner ?? '—'}</span>
                <span class="muted text-xs">Acesso: {accessSpecSummary(s.access_spec)}</span>
                <StatusChip phase={s.phase} />
              </div>
            </li>
          {/each}
        </ul>
      {/if}
      {#if inv.orphaned_promotions.length > 0}
        <div class="orphans">
          <h3 class="orphans__heading">Promoções sem Space ao vivo</h3>
          <p class="muted text-xs">
            Registradas no histórico de promoções, mas o Space não está mais visível em produção
            (excluído/renomeado) — distinto de um Space ao vivo sem promoção.
          </p>
          <ul class="row-list">
            {#each inv.orphaned_promotions as o (o.promotion_id)}
              <li class="row">
                <div class="row__main">
                  <strong>{o.resource_title ?? o.resource_id}</strong>
                  <span class="muted text-xs">{o.resource_id}</span>
                </div>
                <div class="row__meta">
                  <span class="muted text-xs">Dono: {o.owner ?? '—'}</span>
                  <StatusChip phase={o.phase} />
                </div>
              </li>
            {/each}
          </ul>
        </div>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card title="Fila de solicitações de acesso" subtitle="Todas as solicitações, em qualquer estado, em um só lugar.">
    {#await accessP}
      <Skeleton height="3rem" />
    {:then requests}
      {#if requests.length === 0}
        <p class="muted text-sm">Nenhuma solicitação de acesso ainda.</p>
      {:else}
        <ul class="row-list">
          {#each requests as r (r.id)}
            <li class="row">
              <div class="row__main">
                <strong>{r.space_title ?? r.space_id}</strong>
                <span class="muted text-xs">solicitado por {r.requester_email}</span>
                <span class="muted text-xs">{accessSummary(r)}</span>
              </div>
              <div class="row__meta">
                <Badge tone={ACCESS_STATE_TONE[r.state]}>{ACCESS_STATE_LABEL[r.state]}</Badge>
                {#if r.decided_by}<span class="muted text-xs">decidido por {r.decided_by}</span>{/if}
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card title="Auditoria entre promoções" subtitle="Quem mudou o quê, quando — todas as promoções, mais recentes primeiro.">
    {#await auditP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="70%" />
    {:then rows}
      {#if rows.length === 0}
        <p class="muted text-sm">Nenhum evento de auditoria ainda.</p>
      {:else}
        <ol class="row-list">
          {#each rows as e (e.seq + e.promotion_id)}
            <li class="row">
              <div class="row__main">
                <strong>{EVENT_LABEL[e.event_type] ?? e.event_type}</strong>
                <span class="muted text-xs">{e.resource_title ?? e.resource_id}</span>
              </div>
              <div class="row__meta">
                <Badge tone={e.actor_github_login ? 'accent' : 'neutral'}>
                  {e.actor_github_login ?? e.actor_app_email ?? 'sistema'}
                </Badge>
                <time class="muted text-xs">{when(e.github_event_at ?? e.occurred_at)}</time>
              </div>
            </li>
          {/each}
        </ol>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card
    title="Exportações para dev"
    subtitle="Todo Space prod→dev exportado (rehidratado) por este app, mais recente primeiro."
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
  .dev-link {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--accent-hover);
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
  .orphans {
    margin-top: var(--space-4);
    padding-top: var(--space-4);
    border-top: 1px dashed var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .orphans__heading {
    font-size: 0.9rem;
    margin: 0;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

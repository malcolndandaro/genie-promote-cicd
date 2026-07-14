<script lang="ts">
  // S5 (app-ux-overhaul): the Approver's own "Fila de aprovação" — moved OUT of AcessoEspacos.svelte
  // wholesale (D1: separate sections per persona, not merged) and gated on the real Approver
  // persona (S1's is_approver), not is_admin. No new batch actions — today's row content (space,
  // requester, access-summary, note, Aprovar/Negar) was already solid per D8's diagnosis; this
  // slice only relocates it and adds the REQUIRED denial reason.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import { getAccessRequests, decideAccessRequest, ApiError } from '../lib/api';
  import type { AccessRequest } from '../lib/types';

  let pendingP = $state(getAccessRequests('pending'));

  function reload(): void {
    pendingP = getAccessRequests('pending');
  }

  let decidingId = $state<string | null>(null);
  let decideError = $state<string | null>(null);
  // D8: denial requires a reason — the row being denied opens an inline reason field instead of
  // deciding immediately, rather than a per-row textarea always visible for every pending request.
  let denyingId = $state<string | null>(null);
  let denyReason = $state('');

  function accessSummary(r: AccessRequest): string {
    const parts: string[] = [];
    if (r.want_space_permission) parts.push(`Espaço (${r.space_permission_level})`);
    if (r.want_uc_select) parts.push('Dados (UC SELECT)');
    return parts.join(' + ') || '—';
  }

  async function approve(r: AccessRequest): Promise<void> {
    if (decidingId) return;
    decidingId = r.id;
    decideError = null;
    try {
      await decideAccessRequest(r.id, true);
      reload();
    } catch (e) {
      decideError = errorText(e);
    } finally {
      decidingId = null;
    }
  }

  function startDeny(r: AccessRequest): void {
    denyingId = r.id;
    denyReason = '';
    decideError = null;
  }

  function cancelDeny(): void {
    denyingId = null;
    denyReason = '';
  }

  async function confirmDeny(r: AccessRequest): Promise<void> {
    if (decidingId || !denyReason.trim()) return;
    decidingId = r.id;
    decideError = null;
    try {
      await decideAccessRequest(r.id, false, denyReason.trim());
      denyingId = null;
      denyReason = '';
      reload();
    } catch (e) {
      decideError = errorText(e);
    } finally {
      decidingId = null;
    }
  }

  function errorText(e: unknown): string {
    return e instanceof ApiError && e.status === 403
      ? 'Segregação de funções: você não pode aprovar/negar sua própria solicitação.'
      : e instanceof Error
        ? e.message
        : String(e);
  }
</script>

<div class="stack">
  <Card
    title="Fila de aprovação"
    subtitle="Solicitações de acesso aguardando decisão. Você não pode aprovar/negar sua própria solicitação."
  >
    {#if decideError}
      <p class="error" role="alert">{decideError}</p>
    {/if}
    {#await pendingP}
      <Skeleton height="3rem" />
    {:then requests}
      {#if !requests || requests.length === 0}
        <p class="muted text-sm">Nenhuma solicitação pendente.</p>
      {:else}
        <ul class="req-list">
          {#each requests as r (r.id)}
            <li class="req-row">
              <div class="req-row__top">
                <div class="req-row__main">
                  <strong>{r.space_title ?? r.space_id}</strong>
                  <span class="muted text-xs">solicitado por {r.requester_email}</span>
                  <span class="muted text-xs">{accessSummary(r)}</span>
                  {#if r.note}<span class="muted text-xs">"{r.note}"</span>{/if}
                </div>
                <div class="req-row__actions">
                  <Button
                    variant="primary"
                    loading={decidingId === r.id && denyingId !== r.id}
                    disabled={!!decidingId || denyingId === r.id}
                    onclick={() => approve(r)}
                  >
                    Aprovar
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!!decidingId}
                    onclick={() => (denyingId === r.id ? cancelDeny() : startDeny(r))}
                  >
                    Negar
                  </Button>
                </div>
              </div>
              {#if denyingId === r.id}
                <div class="deny-panel">
                  <label class="field">
                    <span class="field__label">Motivo da negativa (obrigatório)</span>
                    <textarea
                      class="field__input"
                      rows="2"
                      bind:value={denyReason}
                      placeholder="Por que esta solicitação está sendo negada?"
                    ></textarea>
                  </label>
                  <div class="deny-panel__actions">
                    <Button variant="outline" onclick={cancelDeny}>Cancelar</Button>
                    <Button
                      variant="primary"
                      loading={decidingId === r.id}
                      disabled={!denyReason.trim() || decidingId === r.id}
                      onclick={() => confirmDeny(r)}
                    >
                      Confirmar negativa
                    </Button>
                  </div>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    {:catch err}
      <p class="error" role="alert">Erro ao carregar a fila: {err instanceof Error ? err.message : String(err)}</p>
    {/await}
  </Card>
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .req-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .req-row {
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .req-row__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .req-row__main {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .req-row__actions {
    display: flex;
    gap: var(--space-2);
  }
  .deny-panel {
    margin-top: var(--space-3);
    padding-top: var(--space-3);
    border-top: 1px dashed var(--border);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .deny-panel__actions {
    display: flex;
    gap: var(--space-2);
    justify-content: flex-end;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .field__label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .field__input {
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
    background: var(--surface);
    color: inherit;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

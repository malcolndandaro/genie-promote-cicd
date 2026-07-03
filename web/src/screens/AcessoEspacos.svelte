<script lang="ts">
  // F3 — self-service access requests: a user who lacks access to a prod Space can request it; a
  // distinct Steward/Admin approves or denies; approval applies the grant via the GOVERNED path
  // (F2's sidecar -> bot PR -> scripts/apply_access.py) — never an app-direct grant/permission call.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import {
    postAccessRequest,
    getAccessRequests,
    decideAccessRequest,
    ApiError,
    isAuthError,
  } from '../lib/api';
  import type { AccessRequest, AccessRequestState, Whoami } from '../lib/types';

  interface Props {
    who: Whoami | null;
  }
  let { who }: Props = $props();

  // --- request form -----------------------------------------------------------
  let spaceId = $state('');
  let spaceTitle = $state('');
  let note = $state('');
  let wantSpacePermission = $state(true);
  let spacePermissionLevel = $state<'CAN_RUN' | 'CAN_VIEW'>('CAN_RUN');
  let wantUcSelect = $state(false);
  let submitting = $state(false);
  let submitError = $state<string | null>(null);
  let submitted = $state(false);

  async function submit(): Promise<void> {
    if (!spaceId.trim() || (!wantSpacePermission && !wantUcSelect) || submitting) return;
    submitting = true;
    submitError = null;
    try {
      await postAccessRequest({
        spaceId: spaceId.trim(),
        spaceTitle: spaceTitle.trim() || undefined,
        note: note.trim() || undefined,
        wantSpacePermission,
        spacePermissionLevel,
        wantUcSelect,
      });
      spaceId = '';
      spaceTitle = '';
      note = '';
      submitted = true;
      mineP = getAccessRequests('mine'); // refresh the status list with the new request
    } catch (e) {
      submitError = e instanceof Error ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }

  // --- requester status view ---------------------------------------------------
  let mineP = $state(getAccessRequests('mine'));

  // --- approver queue (role-gated server-side; the tab is only OFFERED to an admin/steward) -----
  // `who` arrives async (App.svelte awaits /api/whoami), so this starts null and the $effect below
  // fetches once `who.is_admin` becomes true — never read `who` only at construction time.
  let pendingP = $state<Promise<AccessRequest[]> | null>(null);
  $effect(() => {
    if (who?.is_admin && pendingP === null) pendingP = getAccessRequests('pending');
  });

  let decidingId = $state<string | null>(null);
  let decideError = $state<string | null>(null);

  async function decide(r: AccessRequest, approve: boolean): Promise<void> {
    if (decidingId) return;
    decidingId = r.id;
    decideError = null;
    try {
      await decideAccessRequest(r.id, approve);
      pendingP = getAccessRequests('pending');
      mineP = getAccessRequests('mine'); // in case the approver is also viewing their own list
    } catch (e) {
      decideError =
        e instanceof ApiError && e.status === 403
          ? 'Segregação de funções: você não pode aprovar/negar sua própria solicitação.'
          : e instanceof Error
            ? e.message
            : String(e);
    } finally {
      decidingId = null;
    }
  }

  const STATE_TONE: Record<AccessRequestState, 'neutral' | 'success' | 'warning' | 'destructive'> = {
    requested: 'warning',
    approved: 'neutral',
    applied: 'success',
    denied: 'destructive',
  };
  const STATE_LABEL: Record<AccessRequestState, string> = {
    requested: 'Aguardando aprovação',
    approved: 'Aprovado — aplicando',
    applied: 'Acesso concedido',
    denied: 'Negado',
  };

  function accessSummary(r: AccessRequest): string {
    const parts: string[] = [];
    if (r.want_space_permission) parts.push(`Espaço (${r.space_permission_level})`);
    if (r.want_uc_select) parts.push('Dados (UC SELECT)');
    return parts.join(' + ') || '—';
  }
</script>

<div class="stack">
  <Card
    title="Solicitar acesso"
    subtitle="Peça acesso a um Genie Space que você não pode usar. A concessão é revisada por um Steward/Admin e aplicada pelo pipeline governado — nunca diretamente pelo app."
  >
    <form class="stack" onsubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label class="field">
        <span class="field__label">ID do Genie Space</span>
        <input class="field__input" bind:value={spaceId} placeholder="ex.: 01f16e83..." required />
      </label>
      <label class="field">
        <span class="field__label">Nome do espaço (opcional)</span>
        <input class="field__input" bind:value={spaceTitle} placeholder="ex.: Recebíveis" />
      </label>
      <label class="field">
        <span class="field__label">Justificativa (opcional)</span>
        <textarea class="field__input" bind:value={note} rows="2" placeholder="Por que você precisa deste acesso?"
        ></textarea>
      </label>

      <fieldset class="access-kinds">
        <legend>O que você precisa</legend>
        <label class="check-row">
          <input type="checkbox" bind:checked={wantSpacePermission} />
          Usar o Espaço no Genie
          <select class="field__select" bind:value={spacePermissionLevel} disabled={!wantSpacePermission}>
            <option value="CAN_RUN">CAN_RUN</option>
            <option value="CAN_VIEW">CAN_VIEW</option>
          </select>
        </label>
        <label class="check-row">
          <input type="checkbox" bind:checked={wantUcSelect} />
          Ler os dados subjacentes (UC SELECT)
        </label>
      </fieldset>

      {#if submitError}
        <p class="error" role="alert">Não foi possível enviar a solicitação: {submitError}</p>
      {/if}
      {#if submitted && !submitError}
        <p class="muted text-sm">Solicitação enviada — acompanhe o status abaixo.</p>
      {/if}

      <div>
        <Button type="submit" loading={submitting} disabled={!spaceId.trim() || (!wantSpacePermission && !wantUcSelect)}>
          Enviar solicitação
        </Button>
      </div>
    </form>
  </Card>

  <Card title="Minhas solicitações" subtitle="Status das suas solicitações de acesso.">
    {#await mineP}
      <Skeleton height="3rem" />
    {:then requests}
      {#if requests.length === 0}
        <p class="muted text-sm">Nenhuma solicitação ainda.</p>
      {:else}
        <ul class="req-list">
          {#each requests as r (r.id)}
            <li class="req-row">
              <div class="req-row__main">
                <strong>{r.space_title ?? r.space_id}</strong>
                <span class="muted text-xs">{accessSummary(r)}</span>
                {#if r.note}<span class="muted text-xs">"{r.note}"</span>{/if}
              </div>
              <div class="req-row__meta">
                <Badge tone={STATE_TONE[r.state]}>{STATE_LABEL[r.state]}</Badge>
                {#if r.decided_by}<span class="muted text-xs">por {r.decided_by}</span>{/if}
                {#if r.pr_url}
                  <a href={r.pr_url} target="_blank" rel="noopener noreferrer" class="muted text-xs">PR #{r.pr_number}</a>
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

  {#if who?.is_admin}
    <Card
      title="Fila de aprovação"
      subtitle="Solicitações aguardando decisão (Steward/Admin). Você não pode aprovar/negar sua própria solicitação."
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
                <div class="req-row__main">
                  <strong>{r.space_title ?? r.space_id}</strong>
                  <span class="muted text-xs">solicitado por {r.requester_email}</span>
                  <span class="muted text-xs">{accessSummary(r)}</span>
                  {#if r.note}<span class="muted text-xs">"{r.note}"</span>{/if}
                </div>
                <div class="req-row__actions">
                  <Button
                    variant="primary"
                    loading={decidingId === r.id}
                    disabled={!!decidingId}
                    onclick={() => decide(r, true)}
                  >
                    Aprovar
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!!decidingId}
                    onclick={() => decide(r, false)}
                  >
                    Negar
                  </Button>
                </div>
              </li>
            {/each}
          </ul>
        {/if}
      {:catch err}
        <p class="error" role="alert">Erro ao carregar a fila: {err instanceof Error ? err.message : String(err)}</p>
      {/await}
    </Card>
  {/if}
</div>

<style>
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
  .field__select {
    padding: 0.25rem 0.4rem;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font: inherit;
  }
  .access-kinds {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .access-kinds legend {
    font-size: 0.8rem;
    font-weight: 600;
    padding: 0 var(--space-2);
  }
  .check-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.9rem;
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
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    flex-wrap: wrap;
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .req-row__main {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .req-row__meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .req-row__actions {
    display: flex;
    gap: var(--space-2);
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

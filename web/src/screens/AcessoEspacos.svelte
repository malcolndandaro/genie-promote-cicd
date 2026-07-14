<script lang="ts">
  // F3 — self-service access requests: a user who lacks access to a prod Space can request it; a
  // distinct Approver approves or denies (their OWN "Aprovações de acesso" section —
  // FilaAprovacao.svelte, S5); approval applies the grant via the GOVERNED path (F2's sidecar ->
  // bot PR -> scripts/apply_access.py) — never an app-direct grant/permission call.
  //
  // S5 (app-ux-overhaul, D8): the approval queue moved OUT of this screen entirely (even for a
  // caller who ALSO holds the Approver persona — that queue lives in ITS OWN section, per D1's
  // "separate sections per persona, not merged"). This screen is the Business User's own view:
  // request form + status of their own requests, now showing the denial reason (D8) when denied.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import Picker from '../lib/components/Picker.svelte';
  import { postAccessRequest, getAccessRequests, getProdSpaces, isAuthError } from '../lib/api';
  import type { PickerOption } from '../lib/picker';
  import type { AccessRequest, AccessRequestState } from '../lib/types';

  // No `who` prop needed anymore — this screen no longer branches on is_admin/is_approver.

  // --- request form -----------------------------------------------------------
  // G1: the requested Space is PICKED, from the PROD listing (`/api/prod-spaces`) — deliberately
  // NOT the caller's own `getResources()` dev list, since the entire point of this form is asking
  // for access to a prod Space the caller may not be able to see/open themselves yet.
  let space = $state<PickerOption | null>(null);
  let note = $state('');
  let wantSpacePermission = $state(true);
  let spacePermissionLevel = $state<'CAN_RUN' | 'CAN_VIEW'>('CAN_RUN');
  let wantUcSelect = $state(false);
  let submitting = $state(false);
  let submitError = $state<string | null>(null);
  let submitted = $state(false);

  async function searchProdSpaces(q: string): Promise<PickerOption[]> {
    const spaces = await getProdSpaces(q);
    return spaces.map((s) => ({ id: s.id, label: s.title, sublabel: s.id }));
  }

  async function submit(): Promise<void> {
    if (!space || (!wantSpacePermission && !wantUcSelect) || submitting) return;
    submitting = true;
    submitError = null;
    try {
      await postAccessRequest({
        spaceId: space.id,
        spaceTitle: space.label,
        note: note.trim() || undefined,
        wantSpacePermission,
        spacePermissionLevel,
        wantUcSelect,
      });
      space = null;
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

  // Applying the grant is SYNCHRONOUS with approval (decide -> apply_access_request -> mark_applied,
  // all in the one /decide call). So a request that stays `approved` (never reaching `applied`) means
  // the governed apply FAILED — surface that as a warning to the requester, not a forever-spinning
  // "aplicando…" (F3 review should-fix). `applied` is the only success state.
  const STATE_TONE: Record<AccessRequestState, 'neutral' | 'success' | 'warning' | 'destructive'> = {
    requested: 'warning',
    approved: 'destructive',
    applied: 'success',
    denied: 'destructive',
  };
  const STATE_LABEL: Record<AccessRequestState, string> = {
    requested: 'Aguardando aprovação',
    approved: 'Aprovado — falha ao aplicar (contate um admin)',
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
      <Picker
        label="Genie Space"
        placeholder="Buscar Genie Space em produção…"
        search={searchProdSpaces}
        bind:value={space}
      />
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
        <Button type="submit" loading={submitting} disabled={!space || (!wantSpacePermission && !wantUcSelect)}>
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
                <!-- D8: denial reason clarity — REQUIRED server-side (S5), so every denied
                     request has one to show here. -->
                {#if r.state === 'denied' && r.decision_note}
                  <span class="muted text-xs req-row__reason">Motivo: "{r.decision_note}"</span>
                {/if}
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
  .req-row__reason {
    font-style: italic;
  }
  .req-row__meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

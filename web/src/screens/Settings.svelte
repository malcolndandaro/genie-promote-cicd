<script lang="ts">
  // F5 Phase 1 — Settings: configurable roles (Steward/Admin/approver, Lakebase-backed, no redeploy)
  // + the email<->GitHub-username mapping, plus the READ-ONLY GitHub drift panel (US-32..34). Every
  // call here hits a server endpoint gated on the A2-hardened VERIFIED identity — this screen is
  // only ever RENDERED for who?.is_admin (App.svelte), but the real gate is server-side.
  //
  // Phase 2 (writing GitHub's branch-protection / Environment reviewers to CLOSE drift) is
  // explicitly NOT built here — see genie_reviewer/github_drift.py. This screen only ever shows
  // what's configured + what GitHub enforces; it never offers a "fix it" button that would write
  // to GitHub.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import DriftPanel from '../lib/components/DriftPanel.svelte';
  import Picker from '../lib/components/Picker.svelte';
  import {
    getRoles,
    assignRole,
    revokeRole,
    getAdminDrift,
    getPrincipals,
    ApiError,
    isAuthError,
  } from '../lib/api';
  import type { PickerOption } from '../lib/picker';
  import type { RoleName } from '../lib/types';

  let rolesP = $state(getRoles());
  let driftP = $state(getAdminDrift());

  function refresh(): void {
    rolesP = getRoles();
    driftP = getAdminDrift();
  }

  function errorText(err: unknown): string {
    if (isAuthError(err)) {
      return err instanceof ApiError && err.status === 403
        ? 'Sem permissão — esta tela é restrita a Admins.'
        : 'Sessão expirada — recarregue para reautenticar.';
    }
    return `Erro: ${err instanceof Error ? err.message : String(err)}`;
  }

  const ROLE_LABEL: Record<RoleName, string> = {
    steward: 'Steward',
    admin: 'Admin',
    approver: 'Aprovador',
  };

  // --- assign form -------------------------------------------------------------
  // G1: the assigned person is PICKED from the workspace directory (`/api/principals?kind=user`),
  // never a typed email. `UserOption` carries the real email through unchanged from `Principal`
  // (Picker itself only reads/writes id/label/sublabel) — see AccessSpecForm's PrincipalOption for
  // the same pattern. `githubUsername` stays free text: it's an EXTERNAL GitHub identity, not a
  // Databricks-workspace-listable principal, so there's no directory to pick it from.
  type UserOption = PickerOption & { email: string };

  async function searchUsers(q: string): Promise<UserOption[]> {
    const results = await getPrincipals(q, 'user');
    return results
      .filter((p): p is typeof p & { email: string } => !!p.email)
      .map((p) => ({ id: p.id, label: p.display, sublabel: p.email, email: p.email }));
  }

  let person = $state<UserOption | null>(null);
  let role = $state<RoleName>('steward');
  let githubUsername = $state('');
  let submitting = $state(false);
  let submitError = $state<string | null>(null);

  async function submit(): Promise<void> {
    if (!person || submitting) return;
    submitting = true;
    submitError = null;
    try {
      await assignRole({ email: person.email, role, githubUsername: githubUsername.trim() || undefined });
      person = null;
      githubUsername = '';
      refresh();
    } catch (e) {
      submitError = e instanceof Error ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }

  let revokingId = $state<string | null>(null);

  async function revoke(r: { id: string; email: string; role: RoleName }): Promise<void> {
    if (revokingId) return;
    revokingId = r.id;
    try {
      await revokeRole(r.email, r.role);
      refresh();
    } finally {
      revokingId = null;
    }
  }
</script>

<div class="stack">
  <Card
    title="Papéis configurados"
    subtitle="Quem é Steward, Admin ou Aprovador no app — mudanças aqui têm efeito imediato, sem redeploy."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={refresh}>Atualizar</button>
    {/snippet}

    <form class="assign-form" onsubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div class="assign-form__picker">
        <Picker label="Pessoa" placeholder="Buscar usuário…" search={searchUsers} bind:value={person} />
      </div>
      <label class="field">
        <span class="field__label">Papel</span>
        <select class="field__input" bind:value={role}>
          <option value="steward">Steward</option>
          <option value="admin">Admin</option>
          <option value="approver">Aprovador</option>
        </select>
      </label>
      <label class="field">
        <span class="field__label">Usuário do GitHub (opcional)</span>
        <input class="field__input" bind:value={githubUsername} placeholder="ex.: PSPedro176" />
      </label>
      <Button type="submit" loading={submitting} disabled={!person}>Salvar papel</Button>
    </form>
    {#if submitError}
      <p class="error" role="alert">Não foi possível salvar o papel: {submitError}</p>
    {/if}

    {#await rolesP}
      <Skeleton height="3rem" />
    {:then data}
      {#if data.roles.length === 0}
        <p class="muted text-sm">
          Nenhum papel configurado ainda — usando as variáveis de ambiente como padrão inicial
          (Admins: {data.bootstrap_env.admins.join(', ') || '—'}; Stewards:
          {data.bootstrap_env.stewards.join(', ') || '—'}).
        </p>
      {:else}
        <ul class="row-list">
          {#each data.roles as r (r.id)}
            <li class="row">
              <div class="row__main">
                <strong>{r.email}</strong>
                <span class="muted text-xs">
                  {r.github_username ? `GitHub: @${r.github_username}` : 'sem mapeamento do GitHub'}
                </span>
              </div>
              <div class="row__meta">
                <Badge tone="accent">{ROLE_LABEL[r.role]}</Badge>
                <Button
                  variant="ghost"
                  loading={revokingId === r.id}
                  disabled={!!revokingId}
                  onclick={() => revoke(r)}
                >
                  Remover
                </Button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card
    title="Divergência com o GitHub"
    subtitle="Comparação SOMENTE LEITURA entre os papéis do app e os gates que o GitHub realmente aplica (Environment de produção + proteção do branch). O GitHub continua sendo a fonte de verdade — o app nunca escreve esses gates."
  >
    {#await driftP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then report}
      <DriftPanel {report} />
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
  .assign-form {
    display: flex;
    align-items: flex-end;
    gap: var(--space-3);
    flex-wrap: wrap;
    margin-bottom: var(--space-4);
    padding-bottom: var(--space-4);
    border-bottom: 1px solid var(--border);
  }
  .assign-form__picker {
    flex: 1 1 16rem;
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
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

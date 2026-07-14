<script lang="ts">
  // S7a (app-ux-overhaul, D5/GR1): admin registry of Knowledge Assistant endpoints — an ADDITIVE
  // advisory source the reviewer can consult (S7b wires the actual query-during-review), never a
  // replacement for the rules in Configurações. Kept as its OWN screen/nav item rather than folded
  // into Configurações — the original ask was "rules we create in the app" AND "a way to plug
  // knowledge assistant endpoints" as two distinct things, not one crowded screen.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import Picker from '../lib/components/Picker.svelte';
  import {
    getKaEndpoints, createKaEndpoint, updateKaEndpoint, deleteKaEndpoint,
    getServingEndpoints, getResources, isAuthError, ApiError,
  } from '../lib/api';
  import type { PickerOption } from '../lib/picker';

  let endpointsP = $state(getKaEndpoints());

  function reload(): void {
    endpointsP = getKaEndpoints();
  }

  function errorText(err: unknown): string {
    if (isAuthError(err)) {
      return err instanceof ApiError && err.status === 403
        ? 'Sem permissão — esta tela é restrita a Admins.'
        : 'Sessão expirada — recarregue para reautenticar.';
    }
    return `Erro: ${err instanceof Error ? err.message : String(err)}`;
  }

  // --- create form ------------------------------------------------------------
  // RS1: the serving endpoint is PICKED from a live list — never a typed name/id.
  async function searchServingEndpoints(q: string): Promise<PickerOption[]> {
    const endpoints = await getServingEndpoints();
    const filtered = q ? endpoints.filter((e) => e.name.toLowerCase().includes(q.toLowerCase())) : endpoints;
    return filtered.map((e) => ({ id: e.name, label: e.name }));
  }

  // Scope picker draws from the admin's OWN accessible dev spaces (getResources()) — the same
  // source every other space-selection UI in this app uses. Note: this doesn't reach every dev
  // space workspace-wide, only ones the registering admin can themselves see — a reasonable
  // starting scope, not a new admin-wide space-listing endpoint (out of scope for this slice).
  async function searchSpaces(q: string): Promise<PickerOption[]> {
    const resources = await getResources();
    const filtered = q ? resources.filter((r) => r.title.toLowerCase().includes(q.toLowerCase())) : resources;
    return filtered.map((r) => ({ id: r.id, label: r.title, sublabel: r.id }));
  }

  let name = $state('');
  let servingEndpoint = $state<PickerOption | null>(null);
  let scopeMode = $state<'global' | 'scoped'>('global');
  let scopeSpaces = $state<PickerOption[]>([]);
  let submitting = $state(false);
  let submitError = $state<string | null>(null);

  const canSubmit = $derived(
    !!name.trim() && !!servingEndpoint && (scopeMode === 'global' || scopeSpaces.length > 0)
  );

  async function submit(): Promise<void> {
    if (!canSubmit || submitting || !servingEndpoint) return;
    submitting = true;
    submitError = null;
    try {
      await createKaEndpoint({
        name: name.trim(),
        servingEndpointName: servingEndpoint.id,
        isGlobal: scopeMode === 'global',
        scopeSpaceIds: scopeMode === 'global' ? [] : scopeSpaces.map((s) => s.id),
      });
      name = '';
      servingEndpoint = null;
      scopeMode = 'global';
      scopeSpaces = [];
      reload();
    } catch (e) {
      submitError = e instanceof Error ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }

  // --- per-endpoint actions -----------------------------------------------------
  let togglingId = $state<string | null>(null);
  let deletingId = $state<string | null>(null);

  async function toggleEnabled(id: string, enabled: boolean): Promise<void> {
    if (togglingId) return;
    togglingId = id;
    try {
      await updateKaEndpoint(id, { enabled });
      reload();
    } finally {
      togglingId = null;
    }
  }

  async function remove(id: string): Promise<void> {
    if (deletingId) return;
    deletingId = id;
    try {
      await deleteKaEndpoint(id);
      reload();
    } finally {
      deletingId = null;
    }
  }

  function scopeLabel(e: { is_global: boolean; scope_space_ids: string[] }): string {
    if (e.is_global) return 'Todos os espaços';
    return `${e.scope_space_ids.length} espaço(s)`;
  }
</script>

<div class="stack">
  <Card
    title="Registrar endpoint"
    subtitle="Um Knowledge Assistant é uma fonte consultiva ADICIONAL para o revisor — nunca substitui as regras determinísticas/customizadas em Configurações."
  >
    <form class="stack" onsubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label class="field">
        <span class="field__label">Nome</span>
        <input class="field__input" type="text" bind:value={name} placeholder="ex.: Handbook KA" />
      </label>

      <Picker
        label="Serving endpoint"
        placeholder="Buscar endpoint de serving…"
        search={searchServingEndpoints}
        bind:value={servingEndpoint}
      />

      <fieldset class="scope">
        <legend>Escopo</legend>
        <label class="radio-row">
          <input type="radio" name="scope" value="global" bind:group={scopeMode} />
          Todos os espaços (global)
        </label>
        <label class="radio-row">
          <input type="radio" name="scope" value="scoped" bind:group={scopeMode} />
          Espaços específicos
        </label>
        {#if scopeMode === 'scoped'}
          <Picker
            label="Espaços"
            placeholder="Buscar Genie Space…"
            mode="multi"
            search={searchSpaces}
            bind:values={scopeSpaces}
          />
        {/if}
      </fieldset>

      {#if submitError}
        <p class="error" role="alert">Não foi possível registrar: {submitError}</p>
      {/if}

      <div>
        <Button type="submit" loading={submitting} disabled={!canSubmit}>
          Registrar
        </Button>
      </div>
    </form>
  </Card>

  <Card title="Endpoints registrados">
    {#await endpointsP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="70%" />
    {:then endpoints}
      {#if endpoints.length === 0}
        <p class="muted text-sm">Nenhum endpoint registrado ainda.</p>
      {:else}
        <ul class="row-list">
          {#each endpoints as e (e.id)}
            <li class="row">
              <div class="row__main">
                <strong>{e.name}</strong>
                <span class="muted text-xs">{e.serving_endpoint_name}</span>
                <span class="muted text-xs">{scopeLabel(e)}</span>
              </div>
              <div class="row__meta">
                <Badge tone={e.enabled ? 'success' : 'neutral'}>{e.enabled ? 'Ativo' : 'Desativado'}</Badge>
                <Button
                  variant="outline"
                  loading={togglingId === e.id}
                  disabled={!!togglingId}
                  onclick={() => toggleEnabled(e.id, !e.enabled)}
                >
                  {e.enabled ? 'Desativar' : 'Ativar'}
                </Button>
                <Button
                  variant="outline"
                  loading={deletingId === e.id}
                  disabled={!!deletingId}
                  onclick={() => remove(e.id)}
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
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
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
  .scope {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .scope legend {
    font-size: 0.8rem;
    font-weight: 600;
    padding: 0 var(--space-2);
  }
  .radio-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.9rem;
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

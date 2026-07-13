<script lang="ts">
  // G5: the create/overwrite/confirm/run/result state machine for a prod->dev rehydrate — extracted
  // out of `RehydrateAction` so it can be mounted from TWO surfaces: a deployed promotion's own
  // detail (RehydrateAction, which already knows its own promotionId) AND the standalone "Trazer de
  // volta para o dev" screen (`Rehidratar.svelte`, which only has a prod Space picked from
  // `getProdSpaces` — no promotionId at all; the engine resolves it server-side, see G5).
  //
  // Starts straight at 'choosing' — whether/when to SHOW this flow (a trigger button, a picked
  // Space, …) is each caller's own concern, not this component's.
  import Button from './Button.svelte';
  import Picker from './Picker.svelte';
  import { ApiError, isAuthError, postRehydrate, getResources, type RehydrateResult } from '../api';
  import { filterOptions, type PickerOption } from '../picker';
  import { genieSpaceUrl } from '../links';

  interface Props {
    sourceProdSpaceId: string;
    sourceTitle?: string | null;
    /** The Promotion this rehydrate is reseeding FROM — omit when the caller doesn't have one (the
     * standalone screen); the engine resolves it from `sourceProdSpaceId` server-side. */
    promotionId?: string | null;
    /** The dev workspace host (`/api/whoami.dev_host`) — the success state links to the resulting
     * dev Space when present, else just shows its id. */
    devHost?: string | null;
    /** Called after a successful rehydrate (e.g. to refresh an audit trail already on screen). */
    onDone?: () => void;
    /** Called to dismiss/reset the flow — "Cancelar" mid-flow and "Fechar"/"Tentar outro" on
     * done/error. Each caller decides what that means (collapse a panel, clear a picked Space, …). */
    onExit?: () => void;
  }
  let { sourceProdSpaceId, sourceTitle = null, promotionId = null, devHost = null, onDone, onExit }:
    Props = $props();

  type Step = 'choosing' | 'confirming' | 'running' | 'done' | 'error';
  let step = $state<Step>('choosing');
  let mode = $state<'create' | 'overwrite'>('create');
  // G1: the dev Space to overwrite is PICKED from the caller's own dev spaces (`/api/spaces`, the
  // same identity-filtered listing `getResources` already exposes elsewhere) — never a typed id.
  let devSpaceOption = $state<PickerOption | null>(null);
  let result = $state<RehydrateResult | null>(null);
  let error = $state<string | null>(null);

  // Fetched once per component lifetime (the dev space list doesn't change mid-flow) and filtered
  // CLIENT-side on every keystroke via `filterOptions` — small identity-filtered list, no need to
  // round-trip the server per character typed.
  let devSpacesCache: PickerOption[] | null = null;
  async function searchDevSpaces(q: string): Promise<PickerOption[]> {
    if (!devSpacesCache) {
      const resources = await getResources();
      devSpacesCache = resources.map((r) => ({ id: r.id, label: r.title, sublabel: r.id }));
    }
    return filterOptions(devSpacesCache, q);
  }

  function reviewChoice(): void {
    if (mode === 'overwrite' && !devSpaceOption) return; // Button is disabled too; belt & suspenders
    step = 'confirming';
  }

  function back(): void {
    step = 'choosing';
  }

  async function confirm(): Promise<void> {
    step = 'running';
    error = null;
    try {
      result = await postRehydrate({
        sourceProdSpaceId,
        mode,
        devSpaceId: mode === 'overwrite' ? devSpaceOption?.id : undefined,
        title: sourceTitle ? `${sourceTitle} (dev)` : undefined,
        promotionId: promotionId ?? undefined,
      });
      step = 'done';
      onDone?.();
    } catch (e) {
      // The A2 fail-closed guard denies with a plain 403 — surface it as an actionable "no access",
      // never a generic error (a genuinely-denied user must never wonder if something just broke).
      if (e instanceof ApiError && e.status === 403) {
        error = 'Você não tem acesso a este espaço.';
      } else if (isAuthError(e)) {
        error = 'Sessão expirada — recarregue para reautenticar.';
      } else {
        error = e instanceof Error ? e.message : String(e);
      }
      step = 'error';
    }
  }

  function retry(): void {
    step = 'choosing';
    error = null;
  }
</script>

{#if step === 'choosing'}
  <fieldset class="rehydrate__panel">
    <legend>Exportar para dev</legend>
    <label class="rehydrate__option">
      <input type="radio" name="rehydrate-mode-{sourceProdSpaceId}" value="create" bind:group={mode} />
      Criar um novo Space no dev
    </label>
    <label class="rehydrate__option">
      <input type="radio" name="rehydrate-mode-{sourceProdSpaceId}" value="overwrite" bind:group={mode} />
      Sobrescrever um Space existente no dev
    </label>
    {#if mode === 'overwrite'}
      <div class="rehydrate__field">
        <Picker
          label="Space de dev a sobrescrever"
          placeholder="Buscar seus Spaces de dev…"
          search={searchDevSpaces}
          bind:value={devSpaceOption}
        />
      </div>
    {/if}
    <div class="rehydrate__actions">
      {#if onExit}<Button variant="ghost" onclick={onExit}>Cancelar</Button>{/if}
      <Button
        variant="primary"
        disabled={mode === 'overwrite' && !devSpaceOption}
        onclick={reviewChoice}
      >
        Continuar
      </Button>
    </div>
  </fieldset>
{:else if step === 'confirming'}
  <div class="rehydrate__panel" role="alertdialog" aria-label="Confirmar rehidratação">
    <p class="text-sm">
      {#if mode === 'create'}
        Isso vai <strong>criar um novo Space</strong> no dev a partir da versão de produção, rebinding
        <code>prod_</code> → <code>dev_</code>. Confirmar?
      {:else}
        Isso vai <strong>sobrescrever</strong> o Space <strong>{devSpaceOption?.label}</strong>
        (<code>{devSpaceOption?.id}</code>) no dev com a versão de produção, rebinding
        <code>prod_</code> → <code>dev_</code>. Confirmar?
      {/if}
    </p>
    <div class="rehydrate__actions">
      <Button variant="ghost" onclick={back}>Voltar</Button>
      <Button variant="accent" onclick={confirm}>Confirmar rehidratação</Button>
    </div>
  </div>
{:else if step === 'running'}
  <p class="text-sm muted" role="status" aria-busy="true">Rehidratando para dev…</p>
{:else if step === 'done' && result}
  <p class="msg msg--ok" role="status">
    ✓ Space {result.mode === 'create' ? 'criado' : 'atualizado'} no dev:
    <code>{result.space_id}</code>
  </p>
  {#if devHost}
    <a class="rehydrate__link" href={genieSpaceUrl(devHost, result.space_id)} target="_blank" rel="noopener noreferrer">
      Abrir no dev ↗
    </a>
  {/if}
  {#if onExit}<Button variant="ghost" onclick={onExit}>Fechar</Button>{/if}
{:else if step === 'error'}
  <div class="msg msg--fail" role="alert">Não foi possível rehidratar: {error}</div>
  <div class="rehydrate__actions">
    <Button variant="outline" onclick={retry}>Tentar novamente</Button>
    {#if onExit}<Button variant="ghost" onclick={onExit}>Cancelar</Button>{/if}
  </div>
{/if}

<style>
  .rehydrate__panel {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    width: 100%;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: var(--space-4);
  }
  .rehydrate__panel legend {
    font-weight: 600;
    font-size: 0.9rem;
    padding: 0 var(--space-1);
  }
  .rehydrate__option {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.875rem;
  }
  .rehydrate__field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .rehydrate__actions {
    display: flex;
    gap: var(--space-2);
    justify-content: flex-end;
  }
  .rehydrate__link {
    display: inline-block;
    font-weight: 600;
    color: var(--accent-hover);
  }
  .msg {
    border-radius: var(--radius-sm);
    padding: var(--space-3) var(--space-4);
    font-size: 0.875rem;
    font-weight: 500;
  }
  .msg--fail {
    background: var(--destructive-soft);
    border: 1px solid color-mix(in srgb, var(--destructive) 30%, transparent);
    color: var(--destructive);
  }
  .msg--ok {
    margin: 0;
    color: var(--success);
  }
</style>

<script lang="ts">
  import Button from './Button.svelte';
  import Picker from './Picker.svelte';
  import { postRehydrate, isAuthError, getResources, type RehydrateResult } from '../api';
  import { filterOptions, type PickerOption } from '../picker';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
  }
  let { promotion }: Props = $props();

  // Only meaningful once the Space is actually promoted/live in prod — reseeding dev from a
  // promotion that hasn't deployed would rehydrate a Space that doesn't exist there yet.
  let deployed = $derived(promotion.liveStatus?.phase === 'deployed');

  type Step = 'idle' | 'choosing' | 'confirming' | 'running' | 'done' | 'error';
  let step = $state<Step>('idle');
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

  function start(): void {
    step = 'choosing';
    result = null;
    error = null;
  }

  function cancel(): void {
    step = 'idle';
  }

  function reviewChoice(): void {
    if (mode === 'overwrite' && !devSpaceOption) return; // Button is disabled too; belt & suspenders
    step = 'confirming';
  }

  async function confirm(): Promise<void> {
    if (!promotion.resource) return;
    step = 'running';
    error = null;
    try {
      result = await postRehydrate({
        sourceProdSpaceId: promotion.resource.id,
        mode,
        devSpaceId: mode === 'overwrite' ? devSpaceOption?.id : undefined,
        title: promotion.resource.title ? `${promotion.resource.title} (dev)` : undefined,
        promotionId: promotion.promotionId ?? undefined,
      });
      step = 'done';
      void promotion.refreshAudit(); // the "Rehidratado para dev" event shows up right away
    } catch (e) {
      error = isAuthError(e)
        ? 'Sessão expirada — recarregue para reautenticar.'
        : e instanceof Error
          ? e.message
          : String(e);
      step = 'error';
    }
  }
</script>

{#if deployed}
  <div class="rehydrate">
    {#if step === 'idle'}
      <Button variant="outline" onclick={start}>Rehidratar para dev</Button>
      <p class="text-xs muted rehydrate__hint">
        Traz este Space de volta para o dev (após o reset de ~7 dias), sem abrir PR.
      </p>
    {:else if step === 'choosing'}
      <fieldset class="rehydrate__panel">
        <legend>Rehidratar para dev</legend>
        <label class="rehydrate__option">
          <input type="radio" name="rehydrate-mode" value="create" bind:group={mode} />
          Criar um novo Space no dev
        </label>
        <label class="rehydrate__option">
          <input type="radio" name="rehydrate-mode" value="overwrite" bind:group={mode} />
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
          <Button variant="ghost" onclick={cancel}>Cancelar</Button>
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
          <Button variant="ghost" onclick={cancel}>Cancelar</Button>
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
      <Button variant="ghost" onclick={() => (step = 'idle')}>Fechar</Button>
    {:else if step === 'error'}
      <div class="msg msg--fail" role="alert">Não foi possível rehidratar: {error}</div>
      <Button variant="outline" onclick={start}>Tentar novamente</Button>
    {/if}
  </div>
{/if}

<style>
  .rehydrate {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
    margin-top: var(--space-4);
    padding-top: var(--space-4);
    border-top: 1px solid var(--border);
  }
  .rehydrate__hint {
    margin: 0;
  }
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

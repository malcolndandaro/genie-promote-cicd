<script lang="ts">
  // F2 capture UI — lets the Requester DECLARE who may use a promoted Space (Genie Space
  // CAN_RUN/CAN_VIEW) and who may read its data (UC SELECT). The declaration is app-direct + rides
  // the NEXT promotion request (sets promotion.pendingAccessSpec); the governed CI pipeline is what
  // actually applies it (never app-direct). Empty = nothing declared beyond the base consumer group.
  import Button from './Button.svelte';
  import type { Promotion } from '../promotion.svelte';
  import type { AccessSpec } from '../types';

  interface Props {
    promotion: Promotion;
  }
  let { promotion }: Props = $props();

  interface SpaceRow { principal: string; is_group: boolean; level: string }
  interface UcRow { principal: string; is_group: boolean }

  let open = $state(false);
  let spaceRows = $state<SpaceRow[]>([]);
  let ucRows = $state<UcRow[]>([]);

  const addSpaceRow = () => { spaceRows = [...spaceRows, { principal: '', is_group: false, level: 'CAN_RUN' }]; };
  const removeSpaceRow = (i: number) => { spaceRows = spaceRows.filter((_, j) => j !== i); };
  const addUcRow = () => { ucRows = [...ucRows, { principal: '', is_group: false }]; };
  const removeUcRow = (i: number) => { ucRows = ucRows.filter((_, j) => j !== i); };

  // Rebuild the declared AccessSpec from the non-blank rows and hand it to the promotion store, so
  // whatever is declared here rides the next `Solicitar promoção`. All-blank -> undefined (nothing
  // declared). Runs reactively on any row/field change; never reads pendingAccessSpec (no loop).
  $effect(() => {
    const sp = spaceRows
      .filter((r) => r.principal.trim())
      .map((r) => ({ principal: r.principal.trim(), is_group: r.is_group, level: r.level }));
    const uc = ucRows
      .filter((r) => r.principal.trim())
      .map((r) => ({ principal: r.principal.trim(), is_group: r.is_group }));
    const spec: AccessSpec | undefined =
      sp.length === 0 && uc.length === 0 ? undefined : { space_permissions: sp, uc_principals: uc };
    promotion.pendingAccessSpec = spec;
  });
</script>

<div class="access-form">
  <button type="button" class="access-form__toggle" onclick={() => (open = !open)} aria-expanded={open}>
    <span>{open ? '▾' : '▸'}</span>
    Declarar acesso (opcional)
    {#if promotion.pendingAccessSpec}<span class="access-form__badge">declarado</span>{/if}
  </button>

  {#if open}
    <div class="access-form__body">
      <p class="muted text-sm">
        Aplica-se à <strong>próxima promoção</strong> que você solicitar. A aplicação é governada
        (revisada pelo Steward, aplicada pelo pipeline) — nunca direto pelo app.
      </p>

      <fieldset class="access-form__group">
        <legend>Permissões do Espaço (Genie)</legend>
        {#each spaceRows as row, i (i)}
          <div class="access-form__row">
            <input
              class="access-form__input"
              placeholder="usuário (email) ou grupo"
              bind:value={row.principal}
              aria-label="Principal (permissão do espaço)"
            />
            <label class="access-form__check">
              <input type="checkbox" bind:checked={row.is_group} /> grupo
            </label>
            <select class="access-form__select" bind:value={row.level} aria-label="Nível">
              <option value="CAN_RUN">CAN_RUN</option>
              <option value="CAN_VIEW">CAN_VIEW</option>
            </select>
            <Button variant="ghost" onclick={() => removeSpaceRow(i)} ariaLabel="Remover linha">✕</Button>
          </div>
        {/each}
        <Button variant="outline" onclick={addSpaceRow}>＋ Adicionar principal (espaço)</Button>
      </fieldset>

      <fieldset class="access-form__group">
        <legend>Acesso aos dados (UC SELECT)</legend>
        {#each ucRows as row, i (i)}
          <div class="access-form__row">
            <input
              class="access-form__input"
              placeholder="usuário (email) ou grupo"
              bind:value={row.principal}
              aria-label="Principal (SELECT)"
            />
            <label class="access-form__check">
              <input type="checkbox" bind:checked={row.is_group} /> grupo
            </label>
            <Button variant="ghost" onclick={() => removeUcRow(i)} ariaLabel="Remover linha">✕</Button>
          </div>
        {/each}
        <Button variant="outline" onclick={addUcRow}>＋ Adicionar principal (dados)</Button>
      </fieldset>
    </div>
  {/if}
</div>

<style>
  .access-form {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-3);
  }
  .access-form__toggle {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    background: none;
    border: none;
    cursor: pointer;
    font: inherit;
    font-weight: 600;
    color: var(--foreground);
    padding: 0;
  }
  .access-form__badge {
    font-size: 0.7rem;
    font-weight: 600;
    color: var(--accent-foreground, #fff);
    background: var(--accent, #0b7285);
    border-radius: 999px;
    padding: 0.05rem 0.5rem;
  }
  .access-form__body {
    margin-top: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  .access-form__group {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-3);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .access-form__group legend {
    font-size: 0.8rem;
    font-weight: 600;
    padding: 0 var(--space-2);
  }
  .access-form__row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .access-form__input {
    flex: 1 1 12rem;
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
  }
  .access-form__select {
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
  }
  .access-form__check {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.8rem;
    white-space: nowrap;
  }
</style>

<script lang="ts">
  // F2 capture UI — lets the Requester DECLARE who may use a promoted Space (Genie Space
  // CAN_RUN/CAN_VIEW) and who may read its data (UC SELECT). The declaration is app-direct + rides
  // the NEXT promotion request (sets promotion.pendingAccessSpec); the governed CI pipeline is what
  // actually applies it (never app-direct). Empty = nothing declared beyond the base consumer group.
  //
  // G1: every principal here is PICKED from the workspace directory (Picker -> `/api/principals`),
  // never typed — `is_group` is derived from the picked principal's own `type`, so the old manual
  // checkbox is gone too (a picked group can no longer be mis-tagged as a user, or vice versa).
  //
  // G3: rendered ONLY inside `PromotionConfirm` — the CHOSEN space's confirmation step — never as a
  // page-level block. Collapsed by default so skipping the declaration is a single click on
  // "Confirmar promoção" (no need to even open this toggle).
  import Button from './Button.svelte';
  import Picker from './Picker.svelte';
  import { getPrincipals } from '../api';
  import type { PickerOption } from '../picker';
  import type { Promotion } from '../promotion.svelte';
  import type { AccessSpec, Principal } from '../types';

  interface Props {
    promotion: Promotion;
  }
  let { promotion }: Props = $props();

  /** A Picker option carrying its full source `Principal` (type/email) — Picker itself only ever
   * reads/writes `id`/`label`/`sublabel`, so the extra field rides along untouched and is what lets
   * us recover `is_group` + the right wire value (email for a user, display name for a group)
   * without a second lookup. */
  type PrincipalOption = PickerOption & { principal: Principal };

  function toOption(p: Principal): PrincipalOption {
    return {
      id: `${p.type}:${p.id}`,
      label: p.display,
      sublabel: p.type === 'group' ? 'grupo' : p.email ?? undefined,
      principal: p,
    };
  }

  async function searchPrincipals(q: string): Promise<PrincipalOption[]> {
    const results = await getPrincipals(q);
    return results.map(toOption);
  }

  /** The wire value `apply_access.py` resolves against the SDK: a user's email (`userName`), or a
   * group's `displayName` — never the picker's internal `id`. */
  function wireValue(p: Principal): string {
    return p.type === 'group' ? p.display : p.email ?? p.display;
  }

  // `id` is a stable per-row key (NOT the array index) — each row now hosts a STATEFUL Picker
  // instance (open/query/loaded-options), so keying the `{#each}` by position would let a removed
  // middle row's Picker state leak into whichever row shifts into that slot next.
  interface SpaceRow { id: number; principal: PrincipalOption | null; level: string }
  interface UcRow { id: number; principal: PrincipalOption | null }

  let open = $state(false);
  let spaceRows = $state<SpaceRow[]>([]);
  let ucRows = $state<UcRow[]>([]);
  let nextRowId = 0;

  const addSpaceRow = () => { spaceRows = [...spaceRows, { id: nextRowId++, principal: null, level: 'CAN_RUN' }]; };
  const removeSpaceRow = (i: number) => { spaceRows = spaceRows.filter((_, j) => j !== i); };
  const addUcRow = () => { ucRows = [...ucRows, { id: nextRowId++, principal: null }]; };
  const removeUcRow = (i: number) => { ucRows = ucRows.filter((_, j) => j !== i); };

  // Rebuild the declared AccessSpec from the rows with a picked principal and hand it to the
  // promotion store, so whatever is declared here rides the next `Solicitar promoção`. All-blank ->
  // undefined (nothing declared). Runs reactively on any row/field change; never reads
  // pendingAccessSpec (no loop).
  $effect(() => {
    const sp = spaceRows
      .filter((r) => r.principal)
      .map((r) => ({
        principal: wireValue(r.principal!.principal),
        is_group: r.principal!.principal.type === 'group',
        level: r.level,
      }));
    const uc = ucRows
      .filter((r) => r.principal)
      .map((r) => ({
        principal: wireValue(r.principal!.principal),
        is_group: r.principal!.principal.type === 'group',
      }));
    const spec: AccessSpec | undefined =
      sp.length === 0 && uc.length === 0 ? undefined : { space_permissions: sp, uc_principals: uc };
    promotion.pendingAccessSpec = spec;
  });
</script>

<div class="access-form">
  <button type="button" class="access-form__toggle" onclick={() => (open = !open)} aria-expanded={open}>
    <span>{open ? '▾' : '▸'}</span>
    Quem deve usar este espaço em produção? (opcional)
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
        {#each spaceRows as row, i (row.id)}
          <div class="access-form__row">
            <div class="access-form__picker">
              <Picker
                label="Usuário ou grupo"
                placeholder="Buscar usuário ou grupo…"
                search={searchPrincipals}
                bind:value={row.principal}
              />
            </div>
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
        {#each ucRows as row, i (row.id)}
          <div class="access-form__row">
            <div class="access-form__picker">
              <Picker
                label="Usuário ou grupo"
                placeholder="Buscar usuário ou grupo…"
                search={searchPrincipals}
                bind:value={row.principal}
              />
            </div>
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
    align-items: flex-end;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .access-form__picker {
    flex: 1 1 16rem;
  }
  .access-form__select {
    padding: 0.5rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
  }
</style>

<script lang="ts">
  import Button from './Button.svelte';
  import Picker from './Picker.svelte';
  import { getPrincipals } from '../api';
  import type { PickerOption } from '../picker';
  import type { Promotion } from '../promotion.svelte';
  import type { AudienceSpec, Principal } from '../types';

  interface Props { promotion: Promotion }
  let { promotion }: Props = $props();

  type PrincipalOption = PickerOption & { principal: Principal };
  interface Row { id: number; principal: PrincipalOption | null }

  let nextId = 1;
  let rows = $state<Row[]>([{ id: 0, principal: null }]);

  function toOption(principal: Principal): PrincipalOption {
    return {
      id: `${principal.type}:${principal.id}`,
      label: principal.display,
      sublabel: principal.type === 'group' ? 'grupo' : principal.email ?? undefined,
      principal,
    };
  }

  async function search(q: string): Promise<PrincipalOption[]> {
    return (await getPrincipals(q)).map(toOption);
  }

  function wireValue(principal: Principal): string {
    return principal.type === 'group' ? principal.display : principal.email ?? principal.display;
  }

  function add(): void {
    rows = [...rows, { id: nextId++, principal: null }];
  }

  function remove(index: number): void {
    rows = rows.filter((_, i) => i !== index);
    if (rows.length === 0) rows = [{ id: nextId++, principal: null }];
  }

  $effect(() => {
    const principals = rows.filter((row) => row.principal).map((row) => ({
      principal: wireValue(row.principal!.principal),
      is_group: row.principal!.principal.type === 'group',
    }));
    const spec: AudienceSpec | undefined = principals.length ? { principals } : undefined;
    promotion.pendingAudienceSpec = spec;
  });
</script>

<fieldset class="audience" aria-describedby="audience-help">
  <legend>2. Público do Space <span>obrigatório</span></legend>
  <p id="audience-help" class="muted text-sm">
    Escolha quem poderá usar o Space em produção. Todos recebem <strong>CAN_RUN</strong>.
    A promoção não concede SELECT: ausências viram orientação para a fila Terraform da CERC.
  </p>
  {#each rows as row, index (row.id)}
    <div class="audience__row">
      <div class="audience__picker">
        <Picker
          label="Usuário ou grupo"
          placeholder="Buscar no diretório…"
          {search}
          bind:value={row.principal}
        />
      </div>
      <span class="audience__level">CAN_RUN</span>
      <Button variant="ghost" onclick={() => remove(index)} ariaLabel="Remover do público">✕</Button>
    </div>
  {/each}
  <Button variant="outline" onclick={add}>＋ Adicionar pessoa ou grupo</Button>
</fieldset>

<style>
  .audience {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .audience legend {
    padding: 0 var(--space-2);
    font-weight: 700;
  }
  .audience legend span {
    margin-left: var(--space-2);
    color: var(--destructive);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .audience__row {
    display: flex;
    align-items: flex-end;
    gap: var(--space-2);
  }
  .audience__picker { flex: 1; min-width: 0; }
  .audience__level {
    margin-bottom: 0.45rem;
    border-radius: 999px;
    padding: 0.25rem 0.55rem;
    background: var(--surface-inset);
    color: var(--primary);
    font-size: 0.7rem;
    font-weight: 700;
  }
  @media (max-width: 640px) {
    .audience__row { align-items: stretch; flex-wrap: wrap; }
    .audience__picker { flex-basis: 100%; }
  }
</style>

<script lang="ts">
  import Button from './Button.svelte';
  import type { Promotion, Persona } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
  }
  let { promotion }: Props = $props();

  const personas: { value: Persona; label: string }[] = [
    { value: 'author', label: 'Autor' },
    { value: 'steward', label: 'Steward' },
  ];
</script>

<div class="approval">
  <h3 class="approval__heading">Aprovação do Steward</h3>

  <!-- Demo persona toggle: act as the requester (Autor) or the approver (Steward). -->
  <div class="approval__row">
    <span class="text-sm muted">Agir como:</span>
    <div class="seg" role="group" aria-label="Agir como">
      {#each personas as p (p.value)}
        <button
          type="button"
          class={['seg__btn', promotion.persona === p.value && 'seg__btn--active']}
          aria-pressed={promotion.persona === p.value}
          onclick={() => promotion.setPersona(p.value)}
        >
          {p.label}
        </button>
      {/each}
    </div>
  </div>

  <p id="approval-identities" class="text-xs muted approval__identities">
    Solicitante (OBO): {promotion.requesterEmail ?? '—'} · Steward: {promotion.steward ?? '—'}
  </p>

  {#if promotion.approval.state === 'author'}
    <p class="text-sm muted">
      Aguardando o Steward aprovar. Você é o solicitante — não pode aprovar a própria promoção (SoD).
    </p>
  {:else if promotion.approval.state === 'blocked'}
    <div class="msg msg--fail" role="alert">
      Promoção bloqueada por achados BLOCKER — resolva (ex.: /genie-fix) antes de aprovar.
    </div>
  {:else if promotion.approval.state === 'approved'}
    <p class="msg msg--ok" role="status">
      ✓ Aprovado pelo Steward — o gate de produção é liberado; o service principal faz o deploy.
    </p>
  {:else if promotion.approval.state === 'sod'}
    <p class="text-sm muted">
      Segregação de funções: o solicitante não pode aprovar a própria promoção.
    </p>
  {:else}
    <Button variant="accent" ariaDescribedby="approval-identities" onclick={() => promotion.approve()}>
      ✔ Aprovar promoção
    </Button>
  {/if}

  <p class="text-xs muted approval__note">
    A aprovação aqui é uma prévia da política; a separação de funções é imposta no CI/CD (GitHub
    Environment: revisor obrigatório + auto-revisão bloqueada).
  </p>
</div>

<style>
  .approval {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding-top: var(--space-4);
    border-top: 1px solid var(--border);
  }
  .approval__heading {
    font-size: 0.95rem;
  }
  .approval__row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }
  .approval__identities {
    margin: 0;
  }
  .seg {
    display: inline-flex;
    padding: 0.2rem;
    background: var(--surface-inset);
    border-radius: var(--radius-pill);
  }
  .seg__btn {
    appearance: none;
    border: none;
    background: transparent;
    font-family: inherit;
    font-size: 0.82rem;
    font-weight: 500;
    color: var(--muted-foreground);
    padding: 0.35rem 0.9rem;
    border-radius: var(--radius-pill);
    cursor: pointer;
    transition:
      background-color 0.15s ease,
      color 0.15s ease;
  }
  .seg__btn--active {
    background: var(--surface);
    color: var(--primary);
    box-shadow: var(--shadow-sm);
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
  .approval__note {
    margin: 0;
  }
</style>

<script lang="ts">
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
  }
  let { promotion }: Props = $props();

  // The live PR/deploy state drives the Steward's action (GH4): the gate to approve only exists
  // once the PR is merged and the prod deployment is waiting.
  let phase = $derived(promotion.liveStatus?.phase ?? null);
  let approver = $derived(promotion.liveStatus?.deploy?.approver ?? null);
  let deployRunUrl = $derived(promotion.liveStatus?.deploy?.run_url ?? null);
</script>

<div class="approval">
  <h3 class="approval__heading">Aprovação do Steward</h3>

  <p id="approval-identities" class="text-xs muted approval__identities">
    Solicitante (OBO): {promotion.requesterEmail ?? '—'} · Steward: {promotion.steward ?? '—'}
    {#if promotion.isSteward}· você é o Steward{/if}
  </p>

  {#if promotion.approval.state === 'author'}
    {#if promotion.isMine}
      <p class="text-sm muted">
        Aguardando o Steward aprovar. Você é o solicitante — não pode aprovar a própria promoção (SoD).
      </p>
    {:else}
      <p class="text-sm muted">Apenas o Steward aprova esta promoção.</p>
    {/if}
  {:else if promotion.approval.state === 'blocked'}
    <div class="msg msg--fail" role="alert">
      Promoção bloqueada por achados BLOCKER — resolva (ex.: /genie-fix) antes de aprovar.
    </div>
  {:else if phase === 'awaiting_approval' && deployRunUrl}
    <!-- The Steward approves on GitHub with their OWN identity (prevent_self_review holds). -->
    <a
      class="approve-link"
      href={deployRunUrl}
      target="_blank"
      rel="noopener noreferrer"
      aria-describedby="approval-identities"
    >
      ✔ Aprovar no GitHub ↗
    </a>
  {:else if phase === 'deployed'}
    <p class="msg msg--ok" role="status">
      ✓ Implantado em produção{approver ? ` — aprovado por ${approver}` : ''}.
    </p>
  {:else if phase === 'deploying'}
    <p class="text-sm muted">⏳ Implantando em produção…</p>
  {:else if phase === 'deploy_failed'}
    <div class="msg msg--fail" role="alert">
      Falha no deploy de produção — verifique o run no GitHub.
    </div>
  {:else}
    <p class="text-sm muted">
      Após o merge do PR (checagens verdes), o deploy de produção aguardará a aprovação do Steward aqui.
    </p>
  {/if}

  <p class="text-xs muted approval__note">
    A aprovação acontece no GitHub com a identidade do Steward; a separação de funções é imposta no
    CI/CD (GitHub Environment: revisor obrigatório + auto-revisão bloqueada).
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
  .approval__identities {
    margin: 0;
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
  .approve-link {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: 0.6rem 1.25rem;
    border-radius: var(--radius-pill);
    background: var(--accent);
    color: var(--accent-foreground);
    font-size: 0.9rem;
    font-weight: 600;
    text-decoration: none;
    transition: background-color 0.15s ease;
  }
  .approve-link:hover {
    background: var(--accent-hover);
  }
  .approval__note {
    margin: 0;
  }
</style>

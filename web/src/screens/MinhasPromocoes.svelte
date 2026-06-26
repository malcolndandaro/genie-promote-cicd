<script lang="ts">
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import PromotionList from '../lib/components/PromotionList.svelte';
  import { getPromotions, ApiError, type PromotionSummary } from '../lib/api';
  import type { Whoami } from '../lib/types';

  interface Props {
    who: Whoami | null;
    /** Open a promotion (loads its stored snapshot + audit into the review view, no reviewer re-run). */
    onOpen: (summary: PromotionSummary) => void;
  }
  let { who, onOpen }: Props = $props();

  // Admins/Stewards may view ALL promotions (server-enforced); everyone else only their own.
  let scope = $state<'mine' | 'all'>('mine');
  let promotionsP = $state(getPromotions('mine'));

  function load(s: 'mine' | 'all') {
    scope = s;
    promotionsP = getPromotions(s);
  }
</script>

<Card title="Minhas promoções" subtitle="Histórico de promoções — abra uma para ver a revisão e a trilha de auditoria.">
  {#if who?.is_admin}
    <div class="scope" role="group" aria-label="Escopo">
      <Button variant={scope === 'mine' ? 'primary' : 'outline'} onclick={() => load('mine')}>Minhas</Button>
      <Button variant={scope === 'all' ? 'primary' : 'outline'} onclick={() => load('all')}>Todas (Steward/Admin)</Button>
    </div>
  {/if}

  {#await promotionsP}
    <div class="stack">
      <Skeleton height="3.2rem" />
      <Skeleton height="3.2rem" width="80%" />
    </div>
  {:then promotions}
    <PromotionList {promotions} {onOpen} />
  {:catch err}
    <div class="error-state" role="alert">
      <span class="error">
        {#if err instanceof ApiError && err.status === 401}
          Sessão expirada — recarregue para reautenticar.
        {:else if err instanceof ApiError && err.status === 403}
          Sem permissão para ver todas as promoções — contacte o administrador.
        {:else}
          Não foi possível carregar as promoções: {err instanceof Error ? err.message : String(err)}
        {/if}
      </span>
      <Button variant="outline" onclick={() => load(scope)}>Tentar novamente</Button>
    </div>
  {/await}
</Card>

<style>
  .scope {
    display: flex;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
  }
  .error-state {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
</style>

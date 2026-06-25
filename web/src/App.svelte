<script lang="ts">
  import Header from './lib/components/Header.svelte';
  import Card from './lib/components/Card.svelte';
  import Badge from './lib/components/Badge.svelte';
  import Spinner from './lib/components/Spinner.svelte';
  import Button from './lib/components/Button.svelte';
  import { getWhoami, getResources, isAuthError } from './lib/api';
  import { kindMeta } from './lib/resources';

  // SV1 proves the plumbing end-to-end: the FastAPI server reads the user's OBO token in-process
  // and these same-origin calls resolve as the signed-in user. The full screens land in SV2–SV4.
  // Promises live in $state so an error can be retried by re-assigning a fresh one.
  let whoamiP = $state(getWhoami());
  let resourcesP = $state(getResources());

  const reloadResources = () => (resourcesP = getResources());
</script>

<Header>
  {#snippet right()}
    {#await whoamiP then who}
      {#if who.email}
        <span class="identity"><span class="identity__dot"></span>{who.email}</span>
      {/if}
    {/await}
  {/snippet}
</Header>

<main class="container page">
  <Card
    title="Conexão verificada"
    subtitle="On-Behalf-Of (OBO) lido em processo — sem repasse de token entre apps."
  >
    <div class="stack">
      <div class="row">
        <Badge tone="accent">whoami</Badge>
        {#await whoamiP}
          <Spinner label="Resolvendo identidade…" />
        {:then who}
          <span>{who.email ?? '(sem e-mail encaminhado)'}</span>
          {#if who.steward}<span class="muted text-sm">· steward: {who.steward}</span>{/if}
        {:catch err}
          {@render errorState(err, () => (whoamiP = getWhoami()))}
        {/await}
      </div>

      <div class="resources">
        <div class="row">
          <Badge tone="accent">spaces</Badge>
          <span class="muted text-sm">Seus recursos promovíveis (OBO)</span>
        </div>
        {#await resourcesP}
          <Spinner label="Carregando seus espaços…" />
        {:then resources}
          {#if resources.length === 0}
            <p class="muted text-sm">Nenhum recurso encontrado para a sua identidade.</p>
          {:else}
            <ul class="resource-list">
              {#each resources as r (r.id)}
                <li class="resource-item">
                  <span class="resource-item__glyph" aria-hidden="true">{kindMeta(r.kind).glyph}</span>
                  <span class="resource-item__title">{r.title}</span>
                  <Badge tone="neutral">{kindMeta(r.kind).label}</Badge>
                </li>
              {/each}
            </ul>
          {/if}
        {:catch err}
          {@render errorState(err, reloadResources)}
        {/await}
      </div>
    </div>
  </Card>
</main>

{#snippet errorState(err: unknown, retry: () => void)}
  <div class="error-state" role="alert">
    <span class="error">
      {#if isAuthError(err)}
        Sessão expirada — recarregue a página para reautenticar.
      {:else}
        {err instanceof Error ? err.message : String(err)}
      {/if}
    </span>
    {#if isAuthError(err)}
      <Button variant="outline" onclick={() => location.reload()}>Recarregar</Button>
    {:else}
      <Button variant="outline" onclick={retry}>Tentar novamente</Button>
    {/if}
  </div>
{/snippet}

<style>
  .page {
    padding-top: var(--space-6);
    padding-bottom: var(--space-6);
  }
  .identity {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: 0.85rem;
    color: rgba(255, 255, 255, 0.92);
  }
  .identity__dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--accent);
  }
  .resources {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .resource-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .resource-item {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }
  .resource-item__glyph {
    color: var(--accent);
    font-size: 1rem;
  }
  .resource-item__title {
    flex: 1;
    font-weight: 500;
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

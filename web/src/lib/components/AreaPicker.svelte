<script lang="ts">
  // Where a painel gets FILED in the content repo: `src/dashboards/<area>/<name>/`.
  //
  // A native <select> over a CONTROLLED list, never a text input. The area becomes a real directory,
  // so free text would fragment it into `risco`/`Risco`/`risk` — three places nobody can find
  // anything in. The engine re-validates on submit, so this is convenience, not the control.
  import { getBusinessAreas } from '../api';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
    /** The name the resource will get inside the area, derived from the prod title — shown so the
     * author sees the exact path before committing to it. */
    derivedName?: string;
  }
  let { promotion, derivedName = '' }: Props = $props();

  let areasP = $state(getBusinessAreas());
  let selected = $state<string>('');

  // Publish to the shared flow state (mirrors AudienceSpecForm's own $effect — never reads back).
  $effect(() => {
    promotion.pendingArea = selected || undefined;
  });

  // The exact path the promotion will commit to — shown live so the author confirms WHERE before
  // committing, not after reading a PR diff.
  let pathPreview = $derived(
    `src/dashboards/${selected || '<área>'}/${derivedName || '<nome>'}/`,
  );
</script>

<fieldset class="area" aria-describedby="area-help">
  <legend>3. Área de negócio <span>obrigatório</span></legend>
  <p id="area-help" class="muted text-sm">
    Onde este painel fica versionado no repositório de conteúdo. Escolha a área que responde por ele —
    é assim que outras pessoas vão encontrá-lo.
  </p>

  {#await areasP}
    <p class="text-sm muted" role="status" aria-busy="true">Carregando áreas…</p>
  {:then areas}
    <label class="area__field">
      <span class="area__label">Área</span>
      <select class="area__select" bind:value={selected} aria-label="Área de negócio">
        <option value="" disabled>Selecione uma área…</option>
        {#each areas as area (area.key)}
          <option value={area.key}>{area.label}</option>
        {/each}
      </select>
    </label>
    <p class="area__path" aria-live="polite">
      <span class="muted text-xs">Caminho no repositório</span>
      <code>{pathPreview}</code>
    </p>
  {:catch err}
    <p class="text-xs muted">
      Não foi possível carregar as áreas ({err instanceof Error ? err.message : String(err)}) — recarregue
      a página para tentar novamente.
    </p>
  {/await}
</fieldset>

<style>
  .area {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .area legend {
    padding: 0 var(--space-2);
    font-weight: 700;
  }
  .area legend span {
    margin-left: var(--space-2);
    color: var(--destructive);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .area__field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .area__label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .area__select {
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
    background: var(--surface);
    color: inherit;
    width: 100%;
  }
  .area__select:focus {
    outline: 2px solid var(--ring);
    outline-offset: 1px;
  }
  .area__path {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    margin: 0;
  }
  .area__path code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.78rem;
    padding: 0.35rem 0.5rem;
    border-radius: var(--radius-sm);
    background: var(--surface-inset);
    overflow-wrap: anywhere;
  }
</style>

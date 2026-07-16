<script lang="ts">
  // G7: the promotion-time counterpart to G6's rehydrate de-para — an editable prod Space name
  // (pre-filled with the dev title) + a per-table de-para (source dev ref read-only -> destination
  // prod ref editable, default = the plain dev_->prod_ rebind), loaded from `/api/promote/preview`
  // BEFORE the caller commits. Both ride the NEXT `Solicitar promoção` as
  // pendingProdTitle/pendingTableMapping (mirrors AudienceSpecForm's pendingAudienceSpec) — the app
  // writes them to git sidecars (`.title`/`.mapping.json`); CI is what actually applies the
  // mapping + enforces the allowlist (never applied here, see app_logic.request_promotion).
  //
  // Unlike AudienceSpecForm this is never collapsed (the name is a declaration every promotion makes,
  // not an opt-in). A preview load failure degrades gracefully: the name still defaults to the
  // already-selected resource's own title (known synchronously, no network wait) and no mapping
  // override is sent — this is an ADDITIVE capability over a promotion flow that already worked
  // without it, so it must never block "Confirmar promoção".
  import Button from './Button.svelte';
  import { getPromotePreview } from '../api';
  import type { Promotion } from '../promotion.svelte';

  interface Props {
    promotion: Promotion;
  }
  let { promotion }: Props = $props();

  interface MappingRow {
    source: string;
    target: string;
    defaultTarget: string;
  }

  let title = $state('');
  let rows = $state<MappingRow[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let loadedForId: string | null = null;

  async function load(spaceId: string): Promise<void> {
    loadedForId = spaceId;
    loading = true;
    error = null;
    try {
      const preview = await getPromotePreview(spaceId);
      if (loadedForId !== spaceId) return; // selection changed mid-flight
      rows = preview.tables.map((t) => ({ source: t.source, target: t.default_target, defaultTarget: t.default_target }));
      if (!title.trim() && preview.title) title = preview.title; // safety backfill only — never overrides an edit
    } catch (e) {
      if (loadedForId !== spaceId) return;
      rows = [];
      error = e instanceof Error ? e.message : String(e);
    } finally {
      if (loadedForId === spaceId) loading = false;
    }
  }

  // Seed the name synchronously from the resource already on screen (no network wait — the
  // "pré-preenchido com o título de dev" requirement) and kick off the de-para preview whenever a
  // NEW resource is selected. Never re-triggers on `title`/`rows` writes (only `promotion.resource`
  // is read here).
  $effect(() => {
    const resource = promotion.resource;
    if (resource && loadedForId !== resource.id) {
      title = resource.title;
      rows = [];
      error = null;
      void load(resource.id);
    }
  });

  function resetRow(row: MappingRow): void {
    row.target = row.defaultTarget;
  }

  // Publish to the promotion store whenever the editable state changes — mirrors AudienceSpecForm's
  // own $effect (never reads pendingProdTitle/pendingTableMapping back, no loop).
  $effect(() => {
    promotion.pendingProdTitle = title.trim() || undefined;
    const mapping: Record<string, string> = {};
    for (const row of rows) {
      const target = row.target.trim();
      if (target && target !== row.defaultTarget) mapping[row.source] = target;
    }
    promotion.pendingTableMapping = Object.keys(mapping).length > 0 ? mapping : undefined;
  });
</script>

<div class="mapping-form">
  <label class="mapping-form__field">
    <span class="mapping-form__label">Título de produção</span>
    <input
      type="text"
      class="mapping-form__input"
      aria-label="Nome do space em produção"
      bind:value={title}
      placeholder="Nome do Space em produção"
    />
  </label>

  {#if loading}
    <p class="text-sm muted" role="status" aria-busy="true">Carregando tabelas do espaço…</p>
  {:else if error}
    <p class="text-xs muted">
      Não foi possível carregar o de-para de tabelas ({error}) — a promoção usará o rebind padrão dev → prod.
    </p>
  {:else if rows.length > 0}
    <div class="mapping-form__mapping">
      <div class="mapping-form__summary" aria-label="Mapeamento automático de tabelas">
        <strong>{rows.length} {rows.length === 1 ? 'referência' : 'referências'}</strong>
        <span>dev_{rows[0]?.source.split('_')[1]?.split('.')[0] ?? 'catálogo'} → prod_{rows[0]?.source.split('_')[1]?.split('.')[0] ?? 'catálogo'}</span>
      </div>
      <details class="mapping-form__advanced">
        <summary>Opções avançadas de de-para</summary>
        <p class="text-xs muted mapping-form__hint">
          De-para de tabelas — altere somente quando o destino em produção fugir do padrão.
        </p>
        <div class="mapping-form__table-wrap">
          <table class="mapping-form__table">
            <thead>
              <tr>
                <th>Tabela em dev</th>
                <th>Tabela em produção</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each rows as row (row.source)}
                <tr>
                  <td><code>{row.source}</code></td>
                  <td>
                    <input
                      type="text"
                      class="mapping-form__input"
                      aria-label="Tabela em produção para {row.source}"
                      bind:value={row.target}
                    />
                  </td>
                  <td>
                    {#if row.target.trim() !== row.defaultTarget}
                      <Button variant="ghost" onclick={() => resetRow(row)}>Restaurar padrão</Button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  {/if}
</div>

<style>
  .mapping-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .mapping-form__field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .mapping-form__label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .mapping-form__input {
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
    background: var(--surface);
    color: inherit;
    width: 100%;
  }
  .mapping-form__input:focus {
    outline: 2px solid var(--ring);
    outline-offset: 1px;
  }
  .mapping-form__mapping {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .mapping-form__summary {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 0.7rem 0.8rem;
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--accent-soft) 60%, var(--surface));
    color: var(--muted-foreground);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.75rem;
  }
  .mapping-form__summary strong { color: var(--accent-hover); }
  .mapping-form__advanced summary {
    width: fit-content;
    cursor: pointer;
    color: var(--accent-hover);
    font-size: 0.8rem;
    font-weight: 700;
  }
  .mapping-form__advanced[open] summary { margin-bottom: var(--space-3); }
  .mapping-form__hint {
    margin: 0;
  }
  .mapping-form__table-wrap {
    overflow-x: auto;
  }
  .mapping-form__table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  .mapping-form__table th,
  .mapping-form__table td {
    padding: 0.4rem 0.5rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }
  .mapping-form__table code {
    font-size: 0.8rem;
    word-break: break-all;
  }
</style>

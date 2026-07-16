<script lang="ts">
  import { decisionPresentation } from '../decision';
  import type { PromoteStatus } from '../api';
  import type { Review } from '../types';

  interface Props { review: Review; liveStatus?: PromoteStatus | null; mode?: 'decision' | 'evidence' | 'all' }
  let { review, liveStatus = null, mode = 'all' }: Props = $props();

  const decision = $derived(decisionPresentation(review, liveStatus));
  const attempt = $derived(liveStatus?.deploy.attempt ?? null);
  const revisions = $derived(attempt?.revisions ?? liveStatus?.deploy.revisions ?? liveStatus?.revisions ?? null);
  const runUrl = $derived(attempt?.run_url ?? liveStatus?.deploy.run_url ?? null);
  const annotation = $derived(
    attempt?.reason ?? liveStatus?.deploy_detail?.summary ?? liveStatus?.deploy.rejection_reason ?? null,
  );
  const STAGES = [
    ['preflight', 'Preflight'], ['bundle_deploy', 'Conteúdo'], ['resolve_space', 'Resolver Space'],
    ['assert_app_manage', 'Acesso técnico'], ['reconcile_audience', 'Público'],
    ['verify_live_state', 'Verificação'], ['complete', 'Concluído'],
  ] as const;

  function stageState(stage: string): 'done' | 'failed' | 'current' | 'pending' {
    if (attempt?.failed_stage === stage) return 'failed';
    if (attempt?.completed_stages.includes(stage)) return 'done';
    if (attempt?.current_stage === stage) return 'current';
    return 'pending';
  }
</script>

{#if mode !== 'evidence'}
  <section class="decision" data-state={decision.state} data-tone={decision.tone} aria-labelledby="decision-title">
    <div class="decision__stripe" aria-hidden="true"></div>
    <div class="decision__body">
      <p class="decision__category">{decision.category}</p>
      <h2 id="decision-title">{decision.headline}</h2>
      <p class="decision__truth">{decision.productionTruth}</p>
      <div class="decision__action">
        <span>Próxima ação</span>
        <strong>{decision.nextAction}</strong>
      </div>
      <p class="decision__review-summary">{review.gate.summary}</p>
    </div>
  </section>
{/if}

{#if mode !== 'decision'}
  {#if attempt}
    <section class="attempt-timeline" aria-label="Estágios da tentativa de publicação">
      <h3>Etapas da publicação</h3>
      <ol>
        {#each STAGES as [id, label] (id)}
          {@const state = stageState(id)}
          <li data-state={state}>
            <span class="attempt-timeline__node" aria-hidden="true">
              {state === 'done' ? '✓' : state === 'failed' ? '!' : state === 'current' ? '◴' : ''}
            </span>
            <span>{label}</span>
            <span class="visually-hidden">— {state}</span>
          </li>
        {/each}
      </ol>
    </section>
  {/if}

  <details class="support-details">
    <summary>Detalhes para suporte e auditoria</summary>
    <div class="support-details__body">
      {#if attempt}
        <dl>
          <div><dt>Attempt</dt><dd><code>{attempt.attempt_id}</code></dd></div>
          <div><dt>Estado</dt><dd>{attempt.terminal_state}</dd></div>
          <div><dt>Mutação iniciada</dt><dd>{attempt.mutation_started ? 'sim' : 'não'}</dd></div>
          <div><dt>Concluídos</dt><dd>{attempt.completed_stages.join(', ') || 'nenhum'}</dd></div>
          <div><dt>Atual / falhou</dt><dd>{attempt.failed_stage ?? attempt.current_stage ?? '—'}</dd></div>
          <div><dt>Targets conhecidos</dt><dd>
            {#if Object.keys(attempt.target_ids).length}
              {Object.entries(attempt.target_ids).map(([slug, id]) => `${slug}=${id}`).join(', ')}
            {:else}nenhum{/if}
          </dd></div>
        </dl>
      {:else}
        <p class="support-details__unknown">
          Evidência de Deployment Attempt ainda não disponível; o estado de mutação não será inferido.
        </p>
      {/if}
      {#if revisions}
        <div class="support-details__revisions">
          <span>Conteúdo <code>{revisions.content_revision}</code></span>
          <span>Engine <code>{revisions.engine_revision}</code></span>
        </div>
      {/if}
      {#if annotation}
        <div class="support-details__annotation"><strong>Anotação</strong><pre>{annotation}</pre></div>
      {/if}
      {#if runUrl}
        <a href={runUrl} target="_blank" rel="noopener noreferrer">Abrir run exato no provider ↗</a>
      {/if}
    </div>
  </details>
{/if}

<style>
  .decision {
    position: relative;
    display: grid;
    grid-template-columns: 0.35rem 1fr;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .decision__stripe { background: var(--accent); }
  .decision[data-tone='blocked'] .decision__stripe,
  .decision[data-tone='operational'] .decision__stripe { background: var(--destructive); }
  .decision[data-tone='running'] .decision__stripe { background: var(--warning); }
  .decision[data-tone='success'] .decision__stripe { background: var(--success); }
  .decision__body { padding: var(--space-5); }
  .decision__category {
    margin: 0 0 var(--space-2);
    color: var(--muted-foreground);
    font-size: 0.68rem;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .decision h2 {
    font-family: var(--font-display, Georgia, serif);
    font-size: clamp(1.35rem, 3vw, 2rem);
    letter-spacing: -0.025em;
  }
  .decision__truth { margin: var(--space-2) 0 var(--space-4); color: var(--muted-foreground); }
  .decision__action {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-sm);
    background: var(--surface-inset);
  }
  .decision__action span { color: var(--muted-foreground); font-size: 0.68rem; text-transform: uppercase; }
  .decision__action strong { font-size: 0.86rem; }
  .decision__review-summary { margin: var(--space-3) 0 0; font-size: 0.78rem; color: var(--muted-foreground); }
  .attempt-timeline { margin-top: var(--space-5); }
  .attempt-timeline h3 { margin-bottom: var(--space-3); font-size: 0.9rem; }
  .attempt-timeline ol {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: var(--space-2);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .attempt-timeline li {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    color: var(--muted-foreground);
    text-align: center;
    font-size: 0.68rem;
  }
  .attempt-timeline__node {
    display: grid;
    place-items: center;
    width: 1.6rem;
    height: 1.6rem;
    border: 2px solid var(--border-strong);
    border-radius: 50%;
    background: var(--surface);
    font-size: 0.72rem;
    font-weight: 800;
  }
  .attempt-timeline li[data-state='done'] .attempt-timeline__node { border-color: var(--success); color: var(--success); }
  .attempt-timeline li[data-state='failed'] .attempt-timeline__node { border-color: var(--destructive); color: var(--destructive); }
  .attempt-timeline li[data-state='current'] .attempt-timeline__node { border-color: var(--warning); color: var(--warning); }
  .support-details {
    margin-top: var(--space-5);
    border-top: 1px solid var(--border);
    padding-top: var(--space-4);
  }
  .support-details summary { cursor: pointer; font-size: 0.82rem; font-weight: 700; color: var(--accent-hover); }
  .support-details__body {
    margin-top: var(--space-3);
    padding: var(--space-4);
    border-radius: var(--radius-sm);
    background: #0d1f25;
    color: #d9e8e4;
    font-size: 0.76rem;
    overflow-wrap: anywhere;
  }
  .support-details dl { margin: 0; }
  .support-details dl > div { display: grid; grid-template-columns: 9rem 1fr; gap: var(--space-3); padding: 0.25rem 0; }
  .support-details dt { color: #89a49e; }
  .support-details dd { margin: 0; }
  .support-details code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .support-details__revisions { display: flex; flex-direction: column; gap: var(--space-1); margin-top: var(--space-3); }
  .support-details__annotation pre { white-space: pre-wrap; margin: var(--space-1) 0; color: inherit; }
  .support-details a { display: inline-block; margin-top: var(--space-3); color: #79d5c7; }
  .support-details__unknown { margin: 0; color: #f6c878; }
  @media (max-width: 700px) {
    .attempt-timeline ol { grid-template-columns: 1fr; gap: 0; }
    .attempt-timeline li {
      min-height: 2.5rem;
      flex-direction: row;
      text-align: left;
    }
    .attempt-timeline li:not(:last-child)::after {
      content: '';
      position: absolute;
      left: 0.76rem;
      top: 1.6rem;
      bottom: 0;
      width: 2px;
      background: var(--border);
    }
    .support-details dl > div { grid-template-columns: 1fr; gap: 0; }
  }
</style>

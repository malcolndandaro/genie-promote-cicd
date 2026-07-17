<script lang="ts">
  import { decisionPresentation } from '../decision';
  import type { DeploymentStep, PromoteStatus } from '../api';
  import type { Review } from '../types';

  interface Props {
    review: Review;
    liveStatus?: PromoteStatus | null;
    mode?: 'decision' | 'evidence' | 'all';
    evidenceLoading?: boolean;
    evidenceError?: string | null;
    onLoadEvidence?: () => void | Promise<void>;
  }
  let {
    review, liveStatus = null, mode = 'all', evidenceLoading = false, evidenceError = null,
    onLoadEvidence,
  }: Props = $props();

  const decision = $derived(decisionPresentation(review, liveStatus));
  const attempt = $derived(liveStatus?.deploy.attempt ?? null);
  const deploySteps = $derived(liveStatus?.deploy.steps ?? []);
  const revisions = $derived(attempt?.revisions ?? liveStatus?.deploy.revisions ?? liveStatus?.revisions ?? null);
  const runUrl = $derived(attempt?.run_url ?? liveStatus?.deploy.run_url ?? null);
  const annotation = $derived(
    attempt?.reason ?? liveStatus?.deploy_detail?.summary ?? liveStatus?.deploy.rejection_reason ?? null,
  );
  function stepState(step: DeploymentStep): 'done' | 'failed' | 'running' | 'skipped' | 'pending' {
    if (step.conclusion === 'success' || step.conclusion === 'neutral') return 'done';
    if (['failure', 'cancelled', 'timed_out', 'action_required'].includes(step.conclusion ?? '')) return 'failed';
    if (step.conclusion === 'skipped') return 'skipped';
    if (step.status === 'in_progress') return 'running';
    return 'pending';
  }

  function stepLabel(step: DeploymentStep): string {
    const state = stepState(step);
    return state === 'done' ? 'Concluído'
      : state === 'failed' ? (step.conclusion === 'cancelled' ? 'Cancelado' : 'Falhou')
      : state === 'running' ? 'Em execução'
      : state === 'skipped' ? 'Ignorado'
      : 'Pendente';
  }

  function handleEvidenceToggle(event: Event): void {
    if ((event.currentTarget as HTMLDetailsElement).open) void onLoadEvidence?.();
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
  <details class="support-details" ontoggle={handleEvidenceToggle}>
    <summary>Detalhes para suporte e auditoria</summary>
    <div class="support-details__body">
      {#if evidenceLoading}
        <div class="evidence-loading" role="status" aria-label="Carregando detalhes do GitHub Actions">
          <span class="evidence-loading__spinner" aria-hidden="true"></span>
          <span><strong>Carregando steps do GitHub Actions…</strong><small>Consultando o job e suas anotações.</small></span>
        </div>
      {:else if evidenceError}
        <div class="evidence-error" role="alert">
          <span>Não foi possível carregar os detalhes técnicos agora.</span>
          <button type="button" onclick={() => void onLoadEvidence?.()}>Tentar novamente</button>
        </div>
      {/if}
      {#if deploySteps.length > 0}
        <section class="deploy-steps" aria-label="Steps reais do job no GitHub Actions">
          <p class="technical-source">GitHub Actions · fonte oficial</p>
          <h3>Steps reais do job de deploy</h3>
          <p class="technical-intro">
            Mesmos nomes, ordem e estados exibidos no runner. O step destacado é exatamente onde o
            GitHub reportou a falha.
          </p>
          <ol>
            {#each deploySteps as step, index (`${step.job_name}-${step.number}-${step.name}`)}
              {@const state = stepState(step)}
              <li data-state={state}>
                <span class="deploy-step__node" aria-hidden="true">
                  {state === 'done' ? '✓' : state === 'failed' ? '!' : state === 'running' ? '◴' : state === 'skipped' ? '–' : ''}
                </span>
                <span class="deploy-step__identity">
                  <strong>{step.name}</strong>
                  {#if step.job_name}<small>{step.job_name} · step {step.number ?? index + 1}</small>{/if}
                </span>
                <span class="deploy-step__status">{stepLabel(step)}</span>
              </li>
            {/each}
          </ol>
        </section>
      {/if}
      {#if attempt}
        <section class="attempt-evidence" aria-label="Evidência adicional do Deployment Attempt">
          <p class="technical-source">Evidência adicional do app</p>
          <h3>Deployment Attempt</h3>
          <p class="technical-intro">
            Complementa os steps do GitHub com revisões, alvos e confirmação de mutação em produção.
          </p>
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
        </section>
      {:else if !evidenceLoading}
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
  .deploy-steps { margin: 0 0 var(--space-5); }
  .evidence-loading {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: 4.5rem;
    margin-bottom: var(--space-4);
    padding: var(--space-3);
    border: 1px solid #294148;
    border-radius: var(--radius-sm);
    background: #10242a;
  }
  .evidence-loading > span:last-child { display: flex; flex-direction: column; gap: 0.15rem; }
  .evidence-loading small { color: #89a49e; }
  .evidence-loading__spinner {
    width: 1.2rem;
    height: 1.2rem;
    flex: 0 0 auto;
    border: 3px solid #294148;
    border-top-color: #79d5c7;
    border-radius: 50%;
    animation: evidence-spin 0.7s linear infinite;
  }
  @keyframes evidence-spin { to { transform: rotate(360deg); } }
  .evidence-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
    color: #ffb0a8;
  }
  .evidence-error button {
    border: 1px solid #47716a;
    border-radius: var(--radius-sm);
    padding: 0.35rem 0.65rem;
    background: transparent;
    color: #79d5c7;
    cursor: pointer;
  }
  .technical-source {
    margin: 0 0 var(--space-1);
    color: #79d5c7;
    font-size: 0.64rem;
    font-weight: 800;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .deploy-steps h3,
  .attempt-evidence h3 { margin: 0; color: #f1f8f6; font-size: 0.95rem; }
  .technical-intro {
    max-width: 48rem;
    margin: var(--space-1) 0 var(--space-4);
    color: #a9bfba;
    line-height: 1.45;
  }
  .deploy-steps ol {
    display: flex;
    flex-direction: column;
    margin: 0;
    padding: 0;
    list-style: none;
    border: 1px solid #294148;
    border-radius: var(--radius-sm);
    overflow: hidden;
  }
  .deploy-steps li {
    display: grid;
    grid-template-columns: 1.75rem minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-3);
    min-height: 3rem;
    padding: var(--space-2) var(--space-3);
    color: #b9cbc7;
    background: #10242a;
  }
  .deploy-steps li + li { border-top: 1px solid #294148; }
  .deploy-steps li[data-state='failed'] { background: color-mix(in srgb, var(--destructive) 13%, #10242a); }
  .deploy-step__node {
    display: grid;
    place-items: center;
    width: 1.45rem;
    height: 1.45rem;
    border: 2px solid var(--border-strong);
    border-radius: 50%;
    background: #132a31;
    font-size: 0.72rem;
    font-weight: 800;
  }
  .deploy-steps li[data-state='done'] .deploy-step__node { border-color: var(--success); color: var(--success); }
  .deploy-steps li[data-state='failed'] .deploy-step__node { border-color: var(--destructive); color: var(--destructive); }
  .deploy-steps li[data-state='running'] .deploy-step__node { border-color: var(--warning); color: var(--warning); }
  .deploy-steps li[data-state='skipped'] .deploy-step__node { color: #89a49e; }
  .deploy-step__identity { display: flex; flex-direction: column; min-width: 0; }
  .deploy-step__identity strong { color: #e4efec; font-size: 0.79rem; font-weight: 650; }
  .deploy-step__identity small { color: #79958f; font-size: 0.65rem; }
  .deploy-step__status {
    color: #a9bfba;
    font-size: 0.68rem;
    font-weight: 700;
    white-space: nowrap;
  }
  .deploy-steps li[data-state='failed'] .deploy-step__status { color: #ff9b91; }
  .attempt-evidence { padding-top: var(--space-4); border-top: 1px solid #294148; }
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
    .deploy-steps li { grid-template-columns: 1.75rem minmax(0, 1fr); }
    .deploy-step__status { grid-column: 2; }
    .support-details dl > div { grid-template-columns: 1fr; gap: 0; }
  }
</style>

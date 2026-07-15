<script lang="ts">
  // F5 Phase 1 — Settings: configurable roles (Steward/Admin/approver, Lakebase-backed, no redeploy)
  // + the email<->GitHub-username mapping, plus the READ-ONLY GitHub drift panel (US-32..34). Every
  // call here hits a server endpoint gated on the A2-hardened VERIFIED identity — this screen is
  // only ever RENDERED for who?.is_admin (App.svelte), but the real gate is server-side.
  //
  // Phase 2 (writing GitHub's branch-protection / Environment reviewers to CLOSE drift) is
  // explicitly NOT built here — see genie_reviewer/github_drift.py. This screen only ever shows
  // what's configured + what GitHub enforces; it never offers a "fix it" button that would write
  // to GitHub.
  import Card from '../lib/components/Card.svelte';
  import Button from '../lib/components/Button.svelte';
  import Badge from '../lib/components/Badge.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import DriftPanel from '../lib/components/DriftPanel.svelte';
  import Picker from '../lib/components/Picker.svelte';
  import {
    getRoles,
    assignRole,
    revokeRole,
    getAdminDrift,
    getPrincipals,
    getRules,
    upsertRule,
    resetRule,
    getPromptTemplate,
    savePromptTemplate,
    resetPromptTemplate,
    ApiError,
    isAuthError,
  } from '../lib/api';
  import type { PickerOption } from '../lib/picker';
  import type { RoleName, RuleSeverity } from '../lib/types';

  let rolesP = $state(getRoles());
  let driftP = $state(getAdminDrift());
  let rulesP = $state(getRules());
  let promptTemplateP = $state(getPromptTemplate());

  function refresh(): void {
    rolesP = getRoles();
    driftP = getAdminDrift();
    rulesP = getRules();
    promptTemplateP = getPromptTemplate();
  }

  function errorText(err: unknown): string {
    if (isAuthError(err)) {
      return err instanceof ApiError && err.status === 403
        ? 'Sem permissão — esta tela é restrita a Admins.'
        : 'Sessão expirada — recarregue para reautenticar.';
    }
    return `Erro: ${err instanceof Error ? err.message : String(err)}`;
  }

  const ROLE_LABEL: Record<RoleName, string> = {
    steward: 'Steward',
    admin: 'Admin',
    approver: 'Aprovador',
  };

  // --- assign form -------------------------------------------------------------
  // G1: the assigned person is PICKED from the workspace directory (`/api/principals?kind=user`),
  // never a typed email. `UserOption` carries the real email through unchanged from `Principal`
  // (Picker itself only reads/writes id/label/sublabel) — see AccessSpecForm's PrincipalOption for
  // the same pattern. `githubUsername` stays free text: it's an EXTERNAL GitHub identity, not a
  // Databricks-workspace-listable principal, so there's no directory to pick it from.
  type UserOption = PickerOption & { email: string };

  async function searchUsers(q: string): Promise<UserOption[]> {
    const results = await getPrincipals(q, 'user');
    return results
      .filter((p): p is typeof p & { email: string } => !!p.email)
      .map((p) => ({ id: p.id, label: p.display, sublabel: p.email, email: p.email }));
  }

  let person = $state<UserOption | null>(null);
  let role = $state<RoleName>('steward');
  let githubUsername = $state('');
  let submitting = $state(false);
  let submitError = $state<string | null>(null);

  async function submit(): Promise<void> {
    if (!person || submitting) return;
    submitting = true;
    submitError = null;
    try {
      await assignRole({ email: person.email, role, githubUsername: githubUsername.trim() || undefined });
      person = null;
      githubUsername = '';
      refresh();
    } catch (e) {
      submitError = e instanceof Error ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }

  let revokingId = $state<string | null>(null);

  async function revoke(r: { id: string; email: string; role: RoleName }): Promise<void> {
    if (revokingId) return;
    revokingId = r.id;
    try {
      await revokeRole(r.email, r.role);
      refresh();
    } finally {
      revokingId = null;
    }
  }

  // --- G2: rules configuration --------------------------------------------------------------
  // The Settings screen doesn't own a client-side merge — `getRules()` already returns the
  // EFFECTIVE set (server-computed by `rules_config.effective_rules`), the raw override rows (so
  // each hardcoded rule's current override state, or none, is known), and the 9 hardcoded
  // defaults (so "restaurar padrão" can show what it reverts to without a second round-trip).

  const SEVERITIES: RuleSeverity[] = ['BLOCKER', 'SUGGESTION', 'STYLE'];
  const SEVERITY_TONE: Record<RuleSeverity, 'destructive' | 'warning' | 'neutral'> = {
    BLOCKER: 'destructive',
    SUGGESTION: 'warning',
    STYLE: 'neutral',
  };

  let mutatingRuleId = $state<string | null>(null);
  let ruleError = $state<string | null>(null);

  async function withRuleMutation(ruleId: string, fn: () => Promise<unknown>): Promise<void> {
    if (mutatingRuleId) return;
    mutatingRuleId = ruleId;
    ruleError = null;
    try {
      await fn();
      refresh();
    } catch (e) {
      ruleError = e instanceof Error ? e.message : String(e);
    } finally {
      mutatingRuleId = null;
    }
  }

  function toggleEnabled(ruleId: string, override: { enabled: boolean; severity: RuleSeverity | null; params: Record<string, unknown> | null } | undefined, enabled: boolean): void {
    void withRuleMutation(ruleId, () =>
      upsertRule({ ruleId, enabled, severity: override?.severity ?? undefined, params: override?.params ?? undefined }),
    );
  }

  function setSeverity(ruleId: string, override: { enabled: boolean; params: Record<string, unknown> | null } | undefined, severity: RuleSeverity): void {
    void withRuleMutation(ruleId, () =>
      upsertRule({ ruleId, enabled: override?.enabled ?? true, severity, params: override?.params ?? undefined }),
    );
  }

  // Both EVAL-01 knobs (min_benchmarks + eval_run_threshold) live in the SAME override params
  // jsonb — a plain `params: { onlyThisKey }` write would silently WIPE the other one out (the
  // store replaces the whole column, it doesn't merge), so every write here spreads the existing
  // `override?.params` first and overrides just its own key.
  type EvalOverride = { enabled: boolean; severity: RuleSeverity | null; params: Record<string, unknown> | null };

  function setMinBenchmarks(override: EvalOverride | undefined, value: number): void {
    if (!Number.isFinite(value) || value < 0) return;
    void withRuleMutation('EVAL-01', () =>
      upsertRule({
        ruleId: 'EVAL-01', enabled: override?.enabled ?? true, severity: override?.severity ?? undefined,
        params: { ...override?.params, min_benchmarks: Math.trunc(value) },
      }),
    );
  }

  // W3 follow-up: the eval-run pass-rate threshold, edited as a 0-100 percent, stored as a 0-1
  // fraction (`rules_config.eval_run_threshold`) — mirrors `setMinBenchmarks` exactly.
  function setEvalThreshold(override: EvalOverride | undefined, pct: number): void {
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) return;
    void withRuleMutation('EVAL-01', () =>
      upsertRule({
        ruleId: 'EVAL-01', enabled: override?.enabled ?? true, severity: override?.severity ?? undefined,
        params: { ...override?.params, eval_run_threshold: pct / 100 },
      }),
    );
  }

  function resetOne(ruleId: string): void {
    void withRuleMutation(ruleId, () => resetRule(ruleId));
  }

  // --- custom rule form -----------------------------------------------------------------------
  let customRuleId = $state('');
  let customSeverity = $state<RuleSeverity>('SUGGESTION');
  let customContent = $state('');
  let customCitation = $state('');
  let submittingCustom = $state(false);
  let customError = $state<string | null>(null);

  async function submitCustom(): Promise<void> {
    if (submittingCustom || !customRuleId.trim() || !customContent.trim() || !customCitation.trim()) return;
    submittingCustom = true;
    customError = null;
    try {
      await upsertRule({
        ruleId: customRuleId.trim(), isCustom: true, severity: customSeverity,
        content: customContent.trim(), citation: customCitation.trim(),
      });
      customRuleId = '';
      customContent = '';
      customCitation = '';
      customSeverity = 'SUGGESTION';
      refresh();
    } catch (e) {
      customError = e instanceof Error ? e.message : String(e);
    } finally {
      submittingCustom = false;
    }
  }

  // --- S8: reviewer prompt template (persona/policy) --------------------------------------------
  // The admin edits ONLY the persona/policy text (`review_core.DEFAULT_PERSONA`); the PROTECTED_CORE
  // (prompt-injection defense + JSON output schema) is always appended server-side and is NOT
  // editable here. The textarea is pre-filled from the current custom override when one is saved,
  // otherwise from the hardcoded default — so the admin always edits from a valid baseline. A bad
  // template is rejected server-side (400, via one real test review) and surfaced inline; on
  // success we reload so the saved value (and its "custom" state) round-trips through the store.
  let promptText = $state('');
  let savingPrompt = $state(false);
  let resettingPrompt = $state(false);
  let promptError = $state<string | null>(null);

  // Reseed the editable textarea whenever the template (re)loads (initial mount + every refresh()).
  $effect(() => {
    promptTemplateP
      .then((config) => {
        promptText = config.custom ? config.custom.template_text : config.default;
      })
      .catch(() => {
        /* the {#await} block renders the load error — nothing to seed */
      });
  });

  async function savePrompt(): Promise<void> {
    if (savingPrompt || !promptText.trim()) return;
    savingPrompt = true;
    promptError = null;
    try {
      await savePromptTemplate(promptText);
      refresh();
    } catch (e) {
      promptError = e instanceof Error ? e.message : String(e);
    } finally {
      savingPrompt = false;
    }
  }

  async function resetPrompt(): Promise<void> {
    if (resettingPrompt) return;
    resettingPrompt = true;
    promptError = null;
    try {
      await resetPromptTemplate();
      refresh();
    } catch (e) {
      promptError = e instanceof Error ? e.message : String(e);
    } finally {
      resettingPrompt = false;
    }
  }
</script>

<div class="stack">
  <Card
    title="Papéis configurados"
    subtitle="Quem é Steward, Admin ou Aprovador no app — mudanças aqui têm efeito imediato, sem redeploy."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={refresh}>Atualizar</button>
    {/snippet}

    <form class="assign-form" onsubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div class="assign-form__picker">
        <Picker label="Pessoa" placeholder="Buscar usuário…" search={searchUsers} bind:value={person} />
      </div>
      <label class="field">
        <span class="field__label">Papel</span>
        <select class="field__input" bind:value={role}>
          <option value="steward">Steward</option>
          <option value="admin">Admin</option>
          <option value="approver">Aprovador</option>
        </select>
      </label>
      <label class="field">
        <span class="field__label">Usuário do GitHub (opcional)</span>
        <input class="field__input" bind:value={githubUsername} placeholder="ex.: PSPedro176" />
      </label>
      <Button type="submit" loading={submitting} disabled={!person}>Salvar papel</Button>
    </form>
    {#if submitError}
      <p class="error" role="alert">Não foi possível salvar o papel: {submitError}</p>
    {/if}

    {#await rolesP}
      <Skeleton height="3rem" />
    {:then data}
      {#if data.roles.length === 0}
        <p class="muted text-sm">
          Nenhum papel configurado ainda — usando as variáveis de ambiente como padrão inicial
          (Admins: {data.bootstrap_env.admins.join(', ') || '—'}; Stewards:
          {data.bootstrap_env.stewards.join(', ') || '—'}).
        </p>
      {:else}
        <ul class="row-list">
          {#each data.roles as r (r.id)}
            <li class="row">
              <div class="row__main">
                <strong>{r.email}</strong>
                <span class="muted text-xs">
                  {r.github_username ? `GitHub: @${r.github_username}` : 'sem mapeamento do GitHub'}
                </span>
              </div>
              <div class="row__meta">
                <Badge tone="accent">{ROLE_LABEL[r.role]}</Badge>
                <Button
                  variant="ghost"
                  loading={revokingId === r.id}
                  disabled={!!revokingId}
                  onclick={() => revoke(r)}
                >
                  Remover
                </Button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card
    title="Divergência com o GitHub"
    subtitle="Comparação SOMENTE LEITURA entre os papéis do app e os gates que o GitHub realmente aplica (Environment de produção + proteção do branch). O GitHub continua sendo a fonte de verdade — o app nunca escreve esses gates."
  >
    {#await driftP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then report}
      <DriftPanel {report} />
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card
    title="Regras"
    subtitle="As regras do handbook que o agente revisor usa — habilite/desabilite, ajuste severidade e parâmetros, ou adicione uma regra customizada. Mudanças têm efeito imediato, sem redeploy."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={refresh}>Atualizar</button>
    {/snippet}

    {#if ruleError}
      <p class="error" role="alert">Não foi possível salvar a regra: {ruleError}</p>
    {/if}

    {#await rulesP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then data}
      {#if data.hardcoded.length === 0}
        <p class="muted text-sm">Nenhuma regra encontrada.</p>
      {:else}
        <ul class="row-list">
          {#each data.hardcoded as rule (rule.rule_id)}
            {@const override = data.overrides.find((o) => o.rule_id === rule.rule_id)}
            {@const enabled = override?.enabled ?? true}
            {@const severity = override?.severity ?? rule.severity_hint}
            {@const minBenchmarks = (override?.params?.min_benchmarks as number | undefined)
              ?? (rule.params?.min_benchmarks as number | undefined) ?? 2}
            {@const evalThresholdPct = Math.round((
              (override?.params?.eval_run_threshold as number | undefined)
              ?? (rule.params?.eval_run_threshold as number | undefined) ?? 0.8) * 100)}
            <li class="row rule-row" class:rule-row--disabled={!enabled}>
              <div class="row__main">
                <strong>{rule.rule_id}</strong>
                <span class="muted text-xs">{rule.citation}</span>
                {#if !enabled}<span class="muted text-xs">desabilitada</span>{/if}
              </div>
              <div class="row__meta rule-row__controls">
                <label class="toggle">
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={mutatingRuleId === rule.rule_id}
                    onchange={(e) => toggleEnabled(rule.rule_id, override, e.currentTarget.checked)}
                  />
                  <span class="muted text-xs">Habilitada</span>
                </label>
                <label class="field field--inline">
                  <span class="field__label">Severidade</span>
                  <select
                    class="field__input field__input--narrow"
                    value={severity}
                    disabled={mutatingRuleId === rule.rule_id}
                    onchange={(e) => setSeverity(rule.rule_id, override, e.currentTarget.value as RuleSeverity)}
                  >
                    {#each SEVERITIES as s (s)}
                      <option value={s}>{s}</option>
                    {/each}
                  </select>
                </label>
                {#if rule.rule_id === 'EVAL-01'}
                  <label class="field field--inline">
                    <span class="field__label">Mín. benchmarks</span>
                    <input
                      type="number"
                      min="0"
                      class="field__input field__input--narrow"
                      value={minBenchmarks}
                      disabled={mutatingRuleId === rule.rule_id}
                      onchange={(e) => setMinBenchmarks(override, Number(e.currentTarget.value))}
                    />
                  </label>
                  <label class="field field--inline">
                    <span class="field__label">Limiar do eval-run (%)</span>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      class="field__input field__input--narrow"
                      value={evalThresholdPct}
                      disabled={mutatingRuleId === rule.rule_id}
                      onchange={(e) => setEvalThreshold(override, Number(e.currentTarget.value))}
                    />
                  </label>
                {/if}
                <Badge tone={SEVERITY_TONE[severity]}>{severity}</Badge>
                {#if override}
                  <Button
                    variant="ghost"
                    loading={mutatingRuleId === rule.rule_id}
                    disabled={!!mutatingRuleId}
                    onclick={() => resetOne(rule.rule_id)}
                  >
                    Restaurar padrão
                  </Button>
                {/if}
              </div>
            </li>
          {/each}
          {#each data.overrides.filter((o) => o.is_custom) as o (o.rule_id)}
            <li class="row rule-row" class:rule-row--disabled={!o.enabled}>
              <div class="row__main">
                <strong>{o.rule_id}</strong>
                <span class="muted text-xs">{o.citation}</span>
                <span class="muted text-xs">{o.content}</span>
              </div>
              <div class="row__meta">
                <Badge tone={SEVERITY_TONE[o.severity ?? 'SUGGESTION']}>{o.severity}</Badge>
                <Badge tone="accent">Custom</Badge>
                <Button
                  variant="ghost"
                  loading={mutatingRuleId === o.rule_id}
                  disabled={!!mutatingRuleId}
                  onclick={() => resetOne(o.rule_id)}
                >
                  Remover
                </Button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}

      <form class="assign-form" onsubmit={(e) => { e.preventDefault(); void submitCustom(); }}>
        <label class="field">
          <span class="field__label">rule_id</span>
          <input class="field__input" bind:value={customRuleId} placeholder="ex.: SQL-02" />
        </label>
        <label class="field">
          <span class="field__label">Severidade</span>
          <select class="field__input" bind:value={customSeverity}>
            {#each SEVERITIES as s (s)}
              <option value={s}>{s}</option>
            {/each}
          </select>
        </label>
        <label class="field field--grow">
          <span class="field__label">Texto/instrução (PT)</span>
          <input class="field__input" bind:value={customContent} placeholder="ex.: nunca usar SELECT *" />
        </label>
        <label class="field field--grow">
          <span class="field__label">Citação</span>
          <input class="field__input" bind:value={customCitation} placeholder="ex.: Padrão interno de SQL" />
        </label>
        <Button
          type="submit"
          loading={submittingCustom}
          disabled={!customRuleId.trim() || !customContent.trim() || !customCitation.trim()}
        >
          Adicionar regra custom
        </Button>
      </form>
      {#if customError}
        <p class="error" role="alert">Não foi possível adicionar a regra: {customError}</p>
      {/if}
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>

  <Card
    title="Prompt do revisor"
    subtitle="O texto de persona/política que o agente revisor usa. Mudanças têm efeito imediato na próxima revisão, sem redeploy."
  >
    {#snippet actions()}
      <button type="button" class="refresh" onclick={refresh}>Atualizar</button>
    {/snippet}

    <p class="muted text-sm">
      A defesa contra injeção de prompt e o esquema de saída JSON (o "núcleo protegido") são sempre
      aplicados pelo servidor e NÃO são editáveis aqui — você edita apenas a persona/política.
    </p>

    {#await promptTemplateP}
      <Skeleton height="3rem" />
      <Skeleton height="3rem" width="80%" />
    {:then config}
      <div class="prompt-editor">
        {#if config.custom}
          <p class="muted text-xs">
            Template customizado salvo por {config.custom.updated_by} em {config.custom.updated_at}.
          </p>
        {:else}
          <p class="muted text-xs">
            Nenhum template customizado — editando a partir do padrão do sistema.
          </p>
        {/if}
        <label class="field">
          <span class="field__label">Persona/política do revisor</span>
          <textarea class="field__input prompt-textarea" rows="12" bind:value={promptText}></textarea>
        </label>
        <div class="row__meta">
          <Button
            loading={savingPrompt}
            disabled={!promptText.trim() || resettingPrompt}
            onclick={() => savePrompt()}
          >
            Salvar
          </Button>
          {#if config.custom}
            <Button
              variant="ghost"
              loading={resettingPrompt}
              disabled={savingPrompt}
              onclick={() => resetPrompt()}
            >
              Restaurar padrão
            </Button>
          {/if}
        </div>
        {#if promptError}
          <p class="error" role="alert">Não foi possível salvar o prompt: {promptError}</p>
        {/if}
      </div>
    {:catch err}
      <p class="error" role="alert">{errorText(err)}</p>
    {/await}
  </Card>
</div>

<style>
  .stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }
  .refresh {
    appearance: none;
    border: 1px solid var(--border);
    background: var(--surface);
    color: inherit;
    border-radius: var(--radius-sm);
    padding: 0.35rem 0.75rem;
    font: inherit;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
  }
  .refresh:hover {
    background: var(--surface-inset);
  }
  .assign-form {
    display: flex;
    align-items: flex-end;
    gap: var(--space-3);
    flex-wrap: wrap;
    margin-bottom: var(--space-4);
    padding-bottom: var(--space-4);
    border-bottom: 1px solid var(--border);
  }
  .assign-form__picker {
    flex: 1 1 16rem;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .field__label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted-foreground);
  }
  .field__input {
    padding: 0.5rem 0.65rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font: inherit;
    background: var(--surface);
    color: inherit;
  }
  .row-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    flex-wrap: wrap;
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .row__main {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .row__meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .error {
    color: var(--destructive);
    font-size: 0.875rem;
  }
  .field--grow {
    flex: 1 1 14rem;
  }
  .field--inline {
    flex-direction: row;
    align-items: center;
    gap: 0.4rem;
  }
  .field--inline .field__label {
    white-space: nowrap;
  }
  .field__input--narrow {
    width: 6rem;
    padding: 0.3rem 0.5rem;
  }
  .rule-row {
    align-items: flex-start;
  }
  .rule-row--disabled {
    opacity: 0.6;
  }
  .rule-row__controls {
    row-gap: var(--space-2);
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    cursor: pointer;
  }
  .prompt-editor {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    margin-top: var(--space-3);
  }
  .prompt-textarea {
    width: 100%;
    resize: vertical;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.85rem;
    line-height: 1.5;
  }
</style>

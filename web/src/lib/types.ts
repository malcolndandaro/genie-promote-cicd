/**
 * Shared domain types — the JSON contract the FastAPI engine returns.
 * Kept identical to the engine API so the Svelte client is a faithful port of the AppKit app.
 */

/** Who the platform forwarded (OBO) + the configured Steward (SoD) + caller capabilities. */
export interface Whoami {
  email: string | null;
  steward: string | null;
  is_admin: boolean;
  /** Whether the verified caller holds the Steward persona. */
  is_steward: boolean;
  /** The source/CI repo the header's GitHub link points to (config-driven; the SPA falls back to a
   * default if absent). */
  repo_url?: string | null;
  /** The dev workspace host (G5, `APP_DEV_HOST`) — lets the SPA build a deep-link to a Space just
   * rehydrated back into dev. `null`/absent when unconfigured (local/offline; the link is omitted). */
  dev_host?: string | null;
  /** W3: the PROD workspace host the app itself runs in — lets the SPA build an "Abrir Genie em
   * produção" deep-link once a promotion reaches `deployed`. `null`/absent when it can't be
   * resolved (local/offline; the link is omitted). */
  prod_host?: string | null;
}

/**
 * A promotable resource the user owns. Today the engine only returns Genie spaces, but the UI
 * is modeled around a `kind` so AI/BI dashboards (and future Databricks resources) drop in
 * without reshaping the screens. See `resources.ts` for the kind registry.
 */
export type ResourceKind = 'genie_space' | 'dashboard';

export interface PromotableResource {
  id: string;
  title: string;
  kind: ResourceKind;
  /** Which workspace this resource lives in — 'dev' for the spaces listed in "Meus espaços" (they
   * come from the dev authoring workspace via the dev-reader SP), 'prod' for prod-deployed ones.
   * Surfaced as a small badge so the origin is obvious at a glance. Optional/back-compat: undefined
   * renders no env badge. */
  env?: 'dev' | 'prod';
}

/** G1: one workspace-directory principal (SCIM user or group), as returned by `/api/principals` —
 * the ONLY shape a picker ever hands back to a submitted request; a raw typed email/username never
 * reaches the backend as a principal value. */
export interface Principal {
  type: 'user' | 'group';
  id: string;
  display: string;
  email: string | null;
  /** G9: false for a workspace-LOCAL group (built-in `users`/`admins`, or any workspace-scoped
   * custom group) — UC grants reject those outright. Always true for a user (always account-level).
   * Drives the UC-principals picker's exclusion; the Space-permissions picker ignores it (workspace
   * ACLs accept local groups fine). */
  uc_grantable: boolean;
}

/** One reviewer finding (deterministic rule or LLM). */
export interface Finding {
  rule_id: string;
  severity: string;
  message: string;
  citation?: string;
  suggestion?: string;
}

export interface Gate {
  conclusion: string; // 'success' | 'failure' | (neutral)
  blocker_count: number;
  summary: string;
}

/** W3: one benchmark question's re-run outcome, as classified from Genie's real
 * `GenieEvalAssessment` (GOOD/BAD/NEEDS_REVIEW) — see `genie_reviewer.eval_gate._fetch_eval_questions`.
 * `question` is `null` only in the (rare) case the SDK omitted the question text. */
export interface EvalQuestion {
  question: string | null;
  status: 'correct' | 'incorrect' | 'needs_review';
}

export interface EvalResult {
  status: string;
  summary: string;
  pass_rate?: number | null;
  n?: number;
  /** W3: present once a real per-question fetch succeeded (absent for advisory, and for the
   * counters-only fallback when that fetch fails — the panel then shows just the explainer +
   * summary, no list). Already sorted failures-first by the backend. */
  questions?: EvalQuestion[];
  n_correct?: number;
  n_needs_review?: number;
  threshold?: number;
  /** The eval-run id (W3), when a real run backs this result — no UI use yet beyond debugging. */
  run_id?: string;
}

/** Promotion-pipeline step status as emitted by `app_logic.build_timeline`. */
export type StepStatus = 'pass' | 'fail' | 'running' | 'pending';

/** G8: one failing GitHub PR check-run, as attached to the live `checks` verdict — the CI's own
 * (already PT-friendly) output, so the business user learns WHY without opening GitHub. */
export interface CheckDetail {
  name: string;
  conclusion: string | null;
  summary: string;
  details_url: string | null;
}

/** Fix C: WHY a merged PR's prod DEPLOY run failed (e.g. a script crashing on a real declared
 * access) — the first failing job step + a best-effort annotations summary, mirroring
 * `CheckDetail` one level down (a deploy failure is a workflow JOB failing, not a PR check-run). */
export interface DeployDetail {
  failed_step: string;
  summary: string;
  details_url: string | null;
}

export interface TimelineStep {
  key: string;
  label: string;
  status: StepStatus;
  /** G8: only ever set on the `checks` step, when the live GitHub run failed. */
  detail?: CheckDetail[] | null;
}

/** One Genie Space permission entry the Requester declared (F2, system 1). */
export interface SpacePermissionEntry {
  principal: string;
  is_group: boolean;
  level: string; // 'CAN_RUN' | 'CAN_VIEW'
}

/** One UC SELECT-grant principal the Requester declared (F2, system 2). */
export interface AccessPrincipal {
  principal: string;
  is_group: boolean;
}

/**
 * The Requester's declared access for this promotion (F2). Models BOTH systems explicitly —
 * Genie Space permissions (who can open/run the Space) and UC data grants (who can SELECT the
 * underlying tables) — so the Steward reviews and approves exactly what will be enforced by the
 * governed CI pipeline (never applied app-direct). Empty arrays mean nothing was declared.
 */
export interface AccessSpec {
  space_permissions: SpacePermissionEntry[];
  uc_principals: AccessPrincipal[];
}

export interface AudienceEntry {
  principal: string;
  is_group: boolean;
}

/** Público do Space: required desired set; every entry derives to Genie CAN_RUN. */
export interface AudienceSpec {
  principals: AudienceEntry[];
}

export interface Review {
  findings: Finding[];
  gate: Gate;
  eval: EvalResult;
  allowlist_violations: string[];
  consumer_group: string;
  timeline: TimelineStep[];
  /** The declared AccessSpec (F2) — present once the engine always returns it; optional here so an
   * older cached/recovered review payload (pre-F2) still type-checks. */
  access_spec?: AccessSpec;
  audience_spec?: AudienceSpec | null;
}

/** F4: one cross-Promotion audit row — the same shape as a per-promotion audit event, plus which
 * Promotion/resource it belongs to (so the admin can trace an action back to its Promotion). */
export interface AdminAuditRow {
  seq: number;
  event_type: string;
  occurred_at: string;
  actor_github_login: string | null;
  actor_app_email: string | null;
  github_event_at: string | null;
  detail: Record<string, unknown> | null;
  promotion_id: string;
  resource_id: string;
  resource_title: string | null;
}

/** F1 follow-up: one prod->dev rehydrate event ("Exportações para dev") — captured whenever the
 * app (re-)creates a dev Space from a prod one, whether or not that prod Space went through this
 * app's own promotion flow (most prod Spaces have no Promotion row at all; see
 * `promotion_store.RehydrateEvent`). */
export interface RehydrateEventRow {
  id: string;
  resource_id: string;
  resource_title: string | null;
  actor_email: string;
  mode: 'create' | 'overwrite';
  dev_space_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

/** A configurable role — who is Steward/Admin, in-app (Lakebase-backed), plus
 * the email<->GitHub-username mapping used by drift detection. */
export type RoleName = 'steward' | 'admin';

export interface RoleAssignment {
  id: string;
  email: string;
  role: RoleName;
  github_username: string | null;
  created_at: string;
  updated_at: string;
}

/** F5: the roles the app currently sees + which env vars would apply as the bootstrap fallback if
 * the store were empty (surfaced so an admin understands WHY a role is in effect). */
export interface RolesList {
  roles: RoleAssignment[];
  bootstrap_env: { admins: string[]; stewards: string[] };
}

/** F5 Phase 1: one READ-ONLY divergence between the app's role config and GitHub's enforced gates.
 * `severity: 'unknown'` means the GitHub read itself failed/degraded — NEVER treated as "no drift". */
export type DriftSeverity = 'warning' | 'unknown';

export interface DriftFinding {
  kind: string;
  severity: DriftSeverity;
  message: string;
  detail: Record<string, unknown>;
}

export interface DriftReport {
  has_drift: boolean;
  has_unknown: boolean;
  findings: DriftFinding[];
}

/** G2: admin-configurable reviewer rules. `RuleSeverity` mirrors genie_reviewer's SEVERITIES. */
export type RuleSeverity = 'BLOCKER' | 'SUGGESTION' | 'STYLE';

/** One rule in the EFFECTIVE set the reviewer actually grounds on (hardcoded + overrides merged) —
 * same shape as a `handbook_rules.RULES` entry, `params` present only when set (e.g. EVAL-01's
 * `min_benchmarks`). */
export interface EffectiveRule {
  rule_id: string;
  severity_hint: RuleSeverity;
  citation: string;
  content: string;
  params?: Record<string, unknown>;
}

/** The raw override/custom-rule row (`app/rules_store.py`'s `RuleOverride`) — distinct from
 * `EffectiveRule`: this is what an admin configured, not the merged result. */
export interface RuleOverride {
  rule_id: string;
  is_custom: boolean;
  enabled: boolean;
  severity: RuleSeverity | null;
  params: Record<string, unknown> | null;
  content: string | null;
  citation: string | null;
  updated_by: string | null;
  updated_at: string;
}

/** `GET /admin/rules`'s response: the effective set the reviewer uses right now, the raw override
 * rows (so the UI can show each hardcoded rule's override state, or none), and the 9 hardcoded
 * defaults (so a reset's "back to default" values are known without a second call). */
export interface RulesList {
  effective: EffectiveRule[];
  overrides: RuleOverride[];
  hardcoded: EffectiveRule[];
}

/** S7a (app-ux-overhaul, D5/GR1): a registered Knowledge Assistant endpoint — additive advisory
 * source for the reviewer, never a replacement for the rules above. Either `is_global` (applies
 * to every space, always) or scoped to specific `scope_space_ids` — never both, never neither. */
export interface KaEndpoint {
  id: string;
  name: string;
  serving_endpoint_name: string;
  is_global: boolean;
  scope_space_ids: string[];
  enabled: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

/** S8 (app-ux-overhaul): the admin-saved reviewer prompt override — the PERSONA/POLICY text that
 * REPLACES `review_core.DEFAULT_PERSONA`. Only the persona is editable; the PROTECTED_CORE
 * (prompt-injection defense + JSON output schema) is always appended server-side and is NOT here. */
export interface PromptTemplateCustom {
  template_text: string;
  updated_by: string;
  updated_at: string;
}

/** S8: `GET /admin/prompt-template`'s response — the current custom override (`null` when nothing
 * is saved and the hardcoded default is in effect) alongside the hardcoded `default` persona text
 * (so the Settings screen can pre-fill/edit from the default as a baseline and offer a reset). */
export interface PromptTemplateConfig {
  custom: PromptTemplateCustom | null;
  default: string;
}

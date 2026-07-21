/**
 * Same-origin API client. The FastAPI engine serves this SPA, so every call is `/api/*` on the
 * same origin — the server reads the user's OBO token (`x-forwarded-access-token`) in-process and
 * NO token is ever handled client-side.
 */
import type {
  AudienceSpec,
  Whoami,
  PromotableResource,
  Review,
  ResourceKind,
  AdminAuditRow,
  RehydrateEventRow,
  RoleName,
  RolesList,
  GitHubRoles,
  Principal,
  RulesList,
  RuleSeverity,
  PromptTemplateConfig,
  PromptTemplateCustom,
  CheckDetail,
  DeployDetail,
} from './types';
import { spaceToResource } from './resources';

/** A failed API call. `status` lets callers distinguish a 401 (re-auth) from a 502 (engine). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Whether an error means "your session expired / reload" (vs. an engine fault). */
export function isAuthError(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 401 || e.status === 403);
}

async function toError(r: Response): Promise<ApiError> {
  let detail = `HTTP ${r.status}`;
  try {
    const body = (await r.json()) as { detail?: unknown };
    if (typeof body?.detail === 'string') detail = body.detail;
  } catch {
    /* non-JSON error body — keep the status-code message */
  }
  return new ApiError(r.status, detail);
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as T;
}

/** The signed-in user (OBO email) + the configured Steward. */
export function getWhoami(): Promise<Whoami> {
  return getJSON<Whoami>('/api/whoami');
}

/** The signed-in user's promotable resources (OBO). Today: Genie spaces. */
export async function getResources(): Promise<PromotableResource[]> {
  const data = await getJSON<{ spaces?: { space_id: string; title: string }[] }>('/api/spaces');
  return (data.spaces ?? []).map(spaceToResource);
}

// --- G1: prefilled/searchable pickers — spaces + workspace-directory principals -------------------
//
// Every picker in the app resolves to one of these two listings; a free-typed id/email/username is
// NEVER a legal submitted value (only a search query into one of these). `getResources` above
// already covers the caller's OWN dev spaces (identity-filtered); the two below cover the two gaps
// no existing listing filled: every PROD space (for "request access to a space I don't have yet")
// and the workspace's user/group directory (for every principal field).

/** Every prod-deployed Genie Space (id + title only — no owner/access/phase), for pickers that must
 * name a space the caller may NOT already have access to (F3's request-access flow). `q` filters
 * server-side on title. */
export async function getProdSpaces(q = ''): Promise<PromotableResource[]> {
  const data = await getJSON<{ spaces?: { space_id: string; title: string }[] }>(
    `/api/prod-spaces?q=${encodeURIComponent(q)}`
  );
  return (data.spaces ?? []).map(spaceToResource);
}

/** Users + groups of the workspace directory (SCIM), for every principal picker (F2 access
 * audience declarations and role assignment). `q` is a search query, server-filtered;
 * blank returns a prefilled first page. */
export async function getPrincipals(q = '', kind: 'all' | 'user' | 'group' = 'all'): Promise<Principal[]> {
  const data = await getJSON<{ principals?: Principal[] }>(
    `/api/principals?q=${encodeURIComponent(q)}&kind=${kind}`
  );
  return data.principals ?? [];
}

/** The opened/updated promotion PR (GH2). */
export interface PullRequestRef {
  number: number;
  url: string;
}

export interface PromoteResult {
  review: Review;
  /** null on a no-op promotion (`no_change`): the space was already in prod byte-identical, so no
   * PR was opened (an empty PR triggers no CI and its merge never deploys). */
  pr: PullRequestRef | null;
  /** The persisted Promotion id (LB3) — present when a durable store is bound (the deployed app).
   * Absent on a no-op promotion (nothing is persisted without a PR). */
  promotion_id?: string;
  /** True when the promoted content is byte-identical to what's already in prod — nothing to
   * promote. The app shows a "nada a promover" notice instead of tracking a nonexistent PR. */
  no_change?: boolean;
  /** Review stopped on content blockers before any Change Request or production mutation. */
  blocked?: boolean;
}

/**
 * Request a promotion: review the resource AND open (or update) a real GitHub PR with the
 * attributed review comment. Reviews as the user (OBO); opens the PR + comments as the bot; the
 * server persists the Promotion + Review Snapshot (LB3). The resource title/kind are sent so the
 * stored Promotion (and the history/recovery view) shows the resource without a second lookup.
 *
 * `audienceSpec` is the required Público do Space desired set; every principal derives to CAN_RUN.
 *
 * `prodTitle` (G7, optional) overrides `resource.title` as the prod Space name declaration — the
 * confirm step pre-fills it WITH the dev title but lets the caller edit it before requesting;
 * omitted/blank falls back to the dev title exactly as before G7. `tableMapping` (G7, optional) is
 * the Requester's declared table de-para (source dev ref -> desired prod ref overrides) — ALSO
 * only a declaration; CI applies it, never this request.
 */
export async function postPromote(
  resource: PromotableResource,
  audienceSpec: AudienceSpec,
  prodTitle?: string,
  tableMapping?: Record<string, string>
): Promise<PromoteResult> {
  const r = await fetch('/api/promote', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      space_id: resource.id,
      resource_title: prodTitle?.trim() || resource.title,
      resource_kind: resource.kind,
      audience_spec: audienceSpec,
      ...(tableMapping && Object.keys(tableMapping).length > 0 ? { table_mapping: tableMapping } : {}),
    }),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as PromoteResult;
}

/** A persisted Promotion summary (LB3) — for recovery-on-load + the history view (LB5). */
export interface PromotionSummary {
  id: string;
  resource_id: string;
  resource_kind: ResourceKind;
  resource_title: string | null;
  /** The original requester (OBO email, display-only) — so an admin's cross-user view attributes
   * the real requester, not the viewer. */
  requester_email: string | null;
  current_phase: string | null;
  terminal: boolean;
  created_at: string;
  updated_at: string;
  /** G7: the declared table de-para (source dev ref -> prod ref overrides) persisted WITH the
   * Promotion, independent of the `.mapping.json` sidecar's own PR/branch lifetime — so reopening
   * shows exactly what was declared. Empty/null means no overrides (plain dev_->prod_ defaults). */
  table_mapping: Record<string, string> | null;
  audience_spec: AudienceSpec | null;
  change_provider?: string | null;
  external_id?: string | null;
  external_url?: string | null;
  content_revision?: string | null;
  engine_revision?: string | null;
}

/** One append-only audit event (LB4). `actor_github_login` is the AUTHORITATIVE governance identity;
 * `actor_app_email` is display-only (present only on `requested`). */
export interface AuditEvent {
  seq: number;
  event_type: string;
  occurred_at: string;
  actor_github_login: string | null;
  actor_app_email: string | null;
  github_event_at: string | null;
  detail: Record<string, unknown> | null;
}

/** A Promotion + its latest STORED Review Snapshot + PR ref + cached status + audit trail (LB3/LB4). */
export interface PromotionDetail {
  promotion: PromotionSummary;
  review: Review | null;
  pr: PullRequestRef | null;
  live_status: PromoteStatus | null;
  audit: AuditEvent[];
}

/** The caller's promotions, newest first (LB3 `scope=mine`; `scope=all` is role-gated in LB5). */
export async function getPromotions(
  scope: 'mine' | 'all' | 'steward-queue' = 'mine'
): Promise<PromotionSummary[]> {
  const data = await getJSON<{ promotions: PromotionSummary[] }>(`/api/promotions?scope=${scope}`);
  return data.promotions ?? [];
}

/** A single promotion + its stored review + PR ref + audit — renders WITHOUT re-running the reviewer. */
export function getPromotionDetail(id: string): Promise<PromotionDetail> {
  return getJSON<PromotionDetail>(`/api/promotions/${id}`);
}

/** G7: one row of the promotion-preview table de-para. */
export interface PromotePreviewTable {
  /** The DEV ref this row is FOR — the key a `tableMapping` override to `postPromote` must use. */
  source: string;
  /** The plain dev_->prod_ target the CI render would use if this row is left unchanged. */
  default_target: string;
}

export interface PromotePreview {
  /** The dev Space's title — the default the editable prod-name field is pre-filled with. */
  title: string | null;
  tables: PromotePreviewTable[];
}

/** G7: preview a promotion's table de-para BEFORE requesting it — read-only, persists nothing. */
export function getPromotePreview(spaceId: string): Promise<PromotePreview> {
  return getJSON(`/api/promote/preview?space_id=${encodeURIComponent(spaceId)}`);
}

/** The append-only audit trail for a promotion (LB4) — refreshed as the status poll reconciles. */
export async function getPromotionAudit(id: string): Promise<AuditEvent[]> {
  const data = await getJSON<{ audit: AuditEvent[] }>(`/api/promotions/${id}/audit`);
  return data.audit ?? [];
}

/** Live promotion phases (GH3) — where the PR actually is, read from GitHub by the bot. */
export type PromotePhase =
  | 'open'
  | 'checks_running'
  | 'checks_failed'
  | 'awaiting_approval'
  | 'deploying'
  | 'deployed'
  | 'deploy_failed'
  | 'revision_mismatch'
  | 'merged'
  | 'closed';

export interface DeploymentAttemptEvidence {
  version?: number;
  attempt_id: string;
  run_attempt: number;
  revisions: { content_revision: string; engine_revision: string };
  mutation_started: boolean;
  completed_stages: string[];
  current_stage: string | null;
  failed_stage: string | null;
  target_ids: Record<string, string>;
  reason: string | null;
  run_url: string | null;
  terminal_state: 'running' | 'operational_failed' | 'partial_failed' | 'succeeded';
  sequence?: number;
}

/** One workflow step exactly as reported by the deployment provider's jobs endpoint. */
export interface DeploymentStep {
  name: string;
  status: string | null;
  conclusion: string | null;
  number: number | null;
  job_name: string | null;
  details_url: string | null;
}

export interface PromoteStatus {
  provider?: string;
  external_id?: string;
  external_url?: string | null;
  revisions?: { content_revision: string; engine_revision: string } | null;
  pr_state: string;
  merged: boolean;
  checks: 'none' | 'pending' | 'success' | 'failure';
  /** G8: WHY the checks failed — one entry per failing check run, only populated when
   * `checks === 'failure'` (the bot degrades to `null` on any GitHub read hiccup). */
  checks_detail?: CheckDetail[] | null;
  /** The PR's merge-approval gate, read from its reviews (GH5) — reflects GitHub, never asserts. */
  review_decision: 'approved' | 'changes_requested' | 'review_required';
  deploy: {
    status: string;
    conclusion: string | null;
    waiting_approval: boolean;
    run_url: string | null;
    run_id?: number | null;
    approver?: string | null;
    revisions?: { content_revision: string; engine_revision: string } | null;
    rejected?: boolean;
    rejection_reason?: string | null;
    attempt?: DeploymentAttemptEvidence | null;
    steps?: DeploymentStep[];
  };
  /** Fix C: WHY the deploy failed — only populated when `deploy.conclusion === 'failure'` (the
   * bot degrades to `null` on any GitHub read hiccup, same contract as `checks_detail`). */
  deploy_detail?: DeployDetail | null;
  pr_url: string;
  phase: PromotePhase;
  /** W3: the resolved PROD Genie Space id, present ONLY once `phase === 'deployed'` AND the
   * promotion's title matched exactly one live prod Space (never a guess) — lets the SPA render an
   * "Abrir Genie em produção" deep-link via `genieSpaceUrl(prod_host, prod_space_id)`. */
  prod_space_id?: string | null;
}

/** Read the live status of a promotion PR (bot read; reflects GitHub, never asserts a deploy). */
export function getPromoteStatus(
  prNumber: number,
  includeDeploymentEvidence = false,
): Promise<PromoteStatus> {
  const query = includeDeploymentEvidence ? '?include_deployment_evidence=true' : '';
  return getJSON<PromoteStatus>(`/api/promote/${prNumber}/status${query}`);
}

/** A3/F1: one-click prod->dev reseed (no git PR). `mode: 'overwrite'` requires `devSpaceId`. */
export interface RehydrateResult {
  space_id: string;
  mode: 'create' | 'overwrite';
  title: string | null;
}

export async function postRehydrate(opts: {
  sourceProdSpaceId: string;
  mode: 'create' | 'overwrite';
  devSpaceId?: string;
  title?: string;
  promotionId?: string;
  /** G6: source prod ref -> desired dev ref — only entries actually OVERRIDDEN away from the
   * preview's `default_target` (an identity mapping is a harmless no-op, but there's no reason to
   * send it). Omit/empty means "use the plain defaults", same as before G6. */
  tableMapping?: Record<string, string>;
}): Promise<RehydrateResult> {
  const r = await fetch('/api/rehydrate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      source_prod_space_id: opts.sourceProdSpaceId,
      mode: opts.mode,
      dev_space_id: opts.devSpaceId ?? null,
      title: opts.title ?? null,
      promotion_id: opts.promotionId ?? null,
      table_mapping:
        opts.tableMapping && Object.keys(opts.tableMapping).length > 0 ? opts.tableMapping : null,
    }),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as RehydrateResult;
}

/** G6: one row of the rehydrate-preview table de-para. */
export interface RehydratePreviewTable {
  /** The prod ref this row is FOR — the key a `table_mapping` override must use. */
  source: string;
  /** The plain prod_->dev_ target `postRehydrate` would use if this row is left unchanged. */
  default_target: string;
  /** Best-effort: existing dev tables in the same schema, for a suggestion datalist. Always an
   * array (never absent) — empty means "no suggestions", never an error. */
  dev_suggestions: string[];
}

export interface RehydratePreview {
  /** The source Space's prod title — the default the editable dev-title field is pre-filled with. */
  title: string | null;
  tables: RehydratePreviewTable[];
}

/** G6: preview a prod->dev rehydrate BEFORE committing — read-only, persists nothing. */
export function getRehydratePreview(sourceProdSpaceId: string): Promise<RehydratePreview> {
  return getJSON(`/api/rehydrate/preview?space_id=${encodeURIComponent(sourceProdSpaceId)}`);
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as T;
}

/** S4 (app-ux-overhaul, GR4): the standalone Audit page's combinable filters — space, actor
 * (matches either the GitHub login or the display-only app email), and a date range — plus
 * offset-based pagination on top of the existing `limit`. */
export interface AdminAuditQuery {
  limit?: number;
  offset?: number;
  resourceId?: string;
  actor?: string;
  after?: string; // ISO datetime
  before?: string; // ISO datetime
}

/** The cross-Promotion audit trail ("who changed what, when"), newest first. */
export async function getAdminAudit(query: AdminAuditQuery = {}): Promise<AdminAuditRow[]> {
  const { limit = 200, offset = 0, resourceId, actor, after, before } = query;
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (resourceId) params.set('resource_id', resourceId);
  if (actor) params.set('actor', actor);
  if (after) params.set('after', after);
  if (before) params.set('before', before);
  const data = await getJSON<{ audit: AdminAuditRow[] }>(`/api/admin/audit?${params}`);
  return data.audit ?? [];
}

/** Every prod->dev rehydrate ("Exportações para dev") the app has performed, newest first. */
export async function getAdminRehydrateEvents(limit = 200): Promise<RehydrateEventRow[]> {
  const data = await getJSON<{ events: RehydrateEventRow[] }>(`/api/admin/rehydrate-events?limit=${limit}`);
  return data.events ?? [];
}

// --- F5 Phase 1: configurable roles + READ-ONLY GitHub drift detection (server-gated to admins) --

/** Every configured role assignment + which env vars would apply as the bootstrap fallback. */
export function getRoles(): Promise<RolesList> {
  return getJSON('/api/admin/roles');
}

/** Assign (or update) a role for an email — idempotent upsert; takes effect with NO redeploy. */
export function assignRole(opts: {
  email: string;
  role: RoleName;
  githubUsername?: string;
}): Promise<{ role: RolesList['roles'][number] }> {
  return postJSON('/api/admin/roles', {
    email: opts.email,
    role: opts.role,
    github_username: opts.githubUsername ?? null,
  });
}

/** Remove a role assignment. Idempotent — a no-op if it didn't exist. */
export function revokeRole(email: string, role: RoleName): Promise<{ ok: boolean }> {
  return postJSON('/api/admin/roles/revoke', { email, role });
}

/** R1: read-only mirror of GitHub's enforcement gates — who has Write (can merge PRs) and who
 * is a prod Environment required-reviewer (approves deployments). Graceful degradation: if either
 * GitHub read fails, the corresponding field is absent (never empty-list = no one). */
export function getGitHubRoles(): Promise<GitHubRoles> {
  return getJSON('/api/admin/github-roles');
}

// --- G2: admin-configurable reviewer rules (server-gated to admins) -----------------------------

/** The effective rule set (hardcoded + overrides merged) + the raw override rows + the 9 hardcoded
 * defaults. Takes effect on the NEXT review — no redeploy. */
export function getRules(): Promise<RulesList> {
  return getJSON('/api/admin/rules');
}

/** Disable/re-enable a hardcoded rule, override its severity/params, or create/update a custom
 * rule. `isCustom: false` (default) must name one of the 9 hardcoded rule_ids; `isCustom: true`
 * must NOT collide with one and requires severity + content + citation. */
export function upsertRule(opts: {
  ruleId: string;
  isCustom?: boolean;
  enabled?: boolean;
  severity?: RuleSeverity;
  params?: Record<string, unknown>;
  content?: string;
  citation?: string;
}): Promise<{ rule: RulesList['overrides'][number] }> {
  return postJSON('/api/admin/rules', {
    rule_id: opts.ruleId,
    is_custom: opts.isCustom ?? false,
    enabled: opts.enabled ?? true,
    severity: opts.severity ?? null,
    params: opts.params ?? null,
    content: opts.content ?? null,
    citation: opts.citation ?? null,
  });
}

/** Reset a hardcoded rule back to its default, or delete a custom rule entirely. Idempotent — a
 * no-op if there was nothing to reset. */
export function resetRule(ruleId: string): Promise<{ ok: boolean }> {
  return postJSON('/api/admin/rules/reset', { rule_id: ruleId });
}

// --- S8 (app-ux-overhaul): admin-editable reviewer prompt template (persona/policy only) ---------

/** The current custom persona/policy override (`custom: null` when nothing's saved) plus the
 * hardcoded `default` persona text. The PROTECTED_CORE (injection defense + JSON output schema) is
 * appended server-side and is never editable. */
export function getPromptTemplate(): Promise<PromptTemplateConfig> {
  return getJSON('/api/admin/prompt-template');
}

/** Save a new custom persona/policy template. The server REJECTS a template that breaks reviewer
 * output parsing with HTTP 400 (surfaced as an `ApiError` — the message is the returned `detail`),
 * so a bad edit never reaches the store. Takes effect on the NEXT review — no redeploy. */
export function savePromptTemplate(templateText: string): Promise<{ custom: PromptTemplateCustom }> {
  return postJSON('/api/admin/prompt-template', { template_text: templateText });
}

/** Revert to the hardcoded default persona. Idempotent — a no-op if nothing was saved. */
export function resetPromptTemplate(): Promise<{ ok: boolean }> {
  return postJSON('/api/admin/prompt-template/reset', {});
}

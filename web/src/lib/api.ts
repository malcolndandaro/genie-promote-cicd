/**
 * Same-origin API client. The FastAPI engine serves this SPA, so every call is `/api/*` on the
 * same origin — the server reads the user's OBO token (`x-forwarded-access-token`) in-process and
 * NO token is ever handled client-side.
 */
import type {
  AccessSpec,
  Whoami,
  PromotableResource,
  Review,
  ResourceKind,
  AccessRequest,
  AccessRequestAuditEvent,
  InventorySpace,
  OrphanedPromotion,
  AdminAuditRow,
  RoleName,
  RolesList,
  DriftReport,
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

/** The opened/updated promotion PR (GH2). */
export interface PullRequestRef {
  number: number;
  url: string;
}

export interface PromoteResult {
  review: Review;
  pr: PullRequestRef;
  /** The persisted Promotion id (LB3) — present when a durable store is bound (the deployed app). */
  promotion_id?: string;
}

/**
 * Request a promotion: review the resource AND open (or update) a real GitHub PR with the
 * attributed review comment. Reviews as the user (OBO); opens the PR + comments as the bot; the
 * server persists the Promotion + Review Snapshot (LB3). The resource title/kind are sent so the
 * stored Promotion (and the history/recovery view) shows the resource without a second lookup.
 *
 * `accessSpec` (F2, optional) is the Requester's declared access — DECLARATION only (this call);
 * the server writes it to a git sidecar the governed CI pipeline enforces, it never mutates a live
 * grant/permission from this request.
 */
export async function postPromote(
  resource: PromotableResource,
  accessSpec?: AccessSpec
): Promise<PromoteResult> {
  const r = await fetch('/api/promote', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      space_id: resource.id,
      resource_title: resource.title,
      resource_kind: resource.kind,
      ...(accessSpec ? { access_spec: accessSpec } : {}),
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
  pr_number: number | null;
  pr_url: string | null;
  current_phase: string | null;
  terminal: boolean;
  created_at: string;
  updated_at: string;
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
export async function getPromotions(scope: 'mine' | 'all' = 'mine'): Promise<PromotionSummary[]> {
  const data = await getJSON<{ promotions: PromotionSummary[] }>(`/api/promotions?scope=${scope}`);
  return data.promotions ?? [];
}

/** A single promotion + its stored review + PR ref + audit — renders WITHOUT re-running the reviewer. */
export function getPromotionDetail(id: string): Promise<PromotionDetail> {
  return getJSON<PromotionDetail>(`/api/promotions/${id}`);
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
  | 'merged'
  | 'closed';

export interface PromoteStatus {
  pr_state: string;
  merged: boolean;
  checks: 'none' | 'pending' | 'success' | 'failure';
  /** The PR's merge-approval gate, read from its reviews (GH5) — reflects GitHub, never asserts. */
  review_decision: 'approved' | 'changes_requested' | 'review_required';
  deploy: {
    status: string;
    conclusion: string | null;
    waiting_approval: boolean;
    run_url: string | null;
    run_id?: number | null;
    approver?: string | null;
  };
  pr_url: string;
  phase: PromotePhase;
}

/** Read the live status of a promotion PR (bot read; reflects GitHub, never asserts a deploy). */
export function getPromoteStatus(prNumber: number): Promise<PromoteStatus> {
  return getJSON<PromoteStatus>(`/api/promote/${prNumber}/status`);
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
    }),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as RehydrateResult;
}

// --- F3: self-service access requests ---------------------------------------

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as T;
}

/** Request access to a Space the caller can't use. The requester identity is derived server-side
 * from the verified OBO token — never sent by the client. */
export function postAccessRequest(opts: {
  spaceId: string;
  spaceTitle?: string;
  note?: string;
  wantSpacePermission: boolean;
  spacePermissionLevel: 'CAN_RUN' | 'CAN_VIEW';
  wantUcSelect: boolean;
}): Promise<{ request: AccessRequest }> {
  return postJSON('/api/access-requests', {
    space_id: opts.spaceId,
    space_title: opts.spaceTitle ?? null,
    note: opts.note ?? null,
    want_space_permission: opts.wantSpacePermission,
    space_permission_level: opts.spacePermissionLevel,
    want_uc_select: opts.wantUcSelect,
  });
}

/** `scope=mine` (default): the caller's own requests, any state — their status view.
 * `scope=pending`: the approver queue (role-gated server-side to Steward/Admin). */
export async function getAccessRequests(scope: 'mine' | 'pending' = 'mine'): Promise<AccessRequest[]> {
  const data = await getJSON<{ requests: AccessRequest[] }>(`/api/access-requests?scope=${scope}`);
  return data.requests ?? [];
}

export function getAccessRequestDetail(
  id: string
): Promise<{ request: AccessRequest; audit: AccessRequestAuditEvent[] }> {
  return getJSON(`/api/access-requests/${id}`);
}

/** Approve or deny a request. SoD (approver != requester) is enforced server-side (403) — this
 * call surfaces that as an `ApiError`, never silently no-ops. On approval the server applies the
 * grant via the GOVERNED sidecar->PR path (F2) and returns the PR it opened. */
export function decideAccessRequest(
  id: string,
  approve: boolean,
  note?: string
): Promise<{ request: AccessRequest; pr?: PullRequestRef }> {
  return postJSON(`/api/access-requests/${id}/decide`, { approve, note: note ?? null });
}

// --- F4: admin console (server-gated to Steward/Admin — a 403 surfaces as an ApiError) -----------

/** The live prod inventory: every deployed Space joined with its owner/declared access/phase (read
 * fresh from prod on every call — never cached client-side). */
export async function getAdminInventory(): Promise<{
  spaces: InventorySpace[];
  orphaned_promotions: OrphanedPromotion[];
}> {
  return getJSON('/api/admin/inventory');
}

/** Every access request in any state (pending/approved/denied/applied), newest first. */
export async function getAdminAccessRequests(): Promise<AccessRequest[]> {
  const data = await getJSON<{ requests: AccessRequest[] }>('/api/admin/access-requests');
  return data.requests ?? [];
}

/** The cross-Promotion audit trail ("who changed what, when"), newest first. */
export async function getAdminAudit(limit = 200): Promise<AdminAuditRow[]> {
  const data = await getJSON<{ audit: AdminAuditRow[] }>(`/api/admin/audit?limit=${limit}`);
  return data.audit ?? [];
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

/** US-34: drift between the app's role config and GitHub's enforced gates (prod Environment
 * required-reviewers + branch protection), for the Settings screen. READ-ONLY (Phase 1). */
export function getAdminDrift(): Promise<DriftReport> {
  return getJSON('/api/admin/drift');
}

/** US-35: the SAME drift check, contextual to one promotion — surfaced on the review screen for
 * the assigned Steward (visible to the promotion's owner or an admin, same as the promotion itself). */
export function getPromotionDrift(promotionId: string): Promise<DriftReport> {
  return getJSON(`/api/promotions/${promotionId}/drift`);
}

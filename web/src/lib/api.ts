/**
 * Same-origin API client. The FastAPI engine serves this SPA, so every call is `/api/*` on the
 * same origin — the server reads the user's OBO token (`x-forwarded-access-token`) in-process and
 * NO token is ever handled client-side.
 */
import type { Whoami, PromotableResource, Review, ResourceKind } from './types';
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

/** Run the full promotion review for a resource (OBO export + app-SP reviewer). */
export async function postReview(resourceId: string): Promise<Review> {
  const r = await fetch('/api/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ space_id: resourceId }),
  });
  if (!r.ok) throw await toError(r);
  return (await r.json()) as Review;
}

/** The opened/updated promotion PR (GH2). */
export interface PullRequestRef {
  number: number;
  url: string;
}

export interface PromoteResult {
  review: Review;
  pr: PullRequestRef;
}

/**
 * Request a promotion: review the resource AND open (or update) a real GitHub PR with the
 * attributed review comment. Reviews as the user (OBO); opens the PR + comments as the bot; the
 * server persists the Promotion + Review Snapshot (LB3). The resource title/kind are sent so the
 * stored Promotion (and the history/recovery view) shows the resource without a second lookup.
 */
export async function postPromote(resource: PromotableResource): Promise<PromoteResult> {
  const r = await fetch('/api/promote', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      space_id: resource.id,
      resource_title: resource.title,
      resource_kind: resource.kind,
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
  pr_number: number | null;
  pr_url: string | null;
  current_phase: string | null;
  terminal: boolean;
  created_at: string;
  updated_at: string;
}

/** A Promotion + its latest STORED Review Snapshot + PR ref — the recover-on-open payload. */
export interface PromotionDetail {
  promotion: PromotionSummary;
  review: Review | null;
  pr: PullRequestRef | null;
}

/** The caller's promotions, newest first (LB3 `scope=mine`; `scope=all` is role-gated in LB5). */
export async function getPromotions(scope: 'mine' | 'all' = 'mine'): Promise<PromotionSummary[]> {
  const data = await getJSON<{ promotions: PromotionSummary[] }>(`/api/promotions?scope=${scope}`);
  return data.promotions ?? [];
}

/** A single promotion + its stored review + PR ref — renders WITHOUT re-running the reviewer. */
export function getPromotionDetail(id: string): Promise<PromotionDetail> {
  return getJSON<PromotionDetail>(`/api/promotions/${id}`);
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

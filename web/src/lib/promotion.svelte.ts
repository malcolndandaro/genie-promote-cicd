/**
 * Promotion flow state — a small reactive state machine shared across the screens.
 *
 * Using a class with `$state` fields (per Svelte best practice) lets the screens read/drive one
 * source of truth: selection + review (SV2/SV3), the opened PR (GH2), and the live PR/CI/deploy
 * status polled from GitHub (GH3/GH4).
 *
 * `phase` is the local request lifecycle: idle → reviewing → reviewed (or error). The REAL
 * promotion lifecycle (PR checks → merge → prod Environment gate → deploy) lives on GitHub and is
 * reflected via `liveStatus` (see `PromoteStatus.phase`). The Steward approval is NOT done in-app:
 * SoD is the GitHub Environment gate (required reviewer + prevent_self_review), and the UI only
 * deep-links the Steward there + reflects the result. `approval` below is a faithful PREVIEW of
 * that policy (the documented future `/api/approve` would re-check it server-side from the token).
 */
import type { PromotableResource, Review } from './types';
import {
  postPromote,
  getPromoteStatus,
  getPromotions,
  getPromotionDetail,
  getPromotionAudit,
  type PullRequestRef,
  type PromoteStatus,
  type AuditEvent,
  type PromotionSummary,
  type PromotionDetail,
} from './api';

export type PromotionPhase = 'idle' | 'reviewing' | 'reviewed' | 'error';

/** The approval view for the CURRENT promotion + viewer (mirrors can_approve + the BLOCKER gate).
 * The actual approval happens on GitHub (the Environment gate), reflected via `liveStatus`. */
export type ApprovalState = 'author' | 'blocked' | 'ready';

export class Promotion {
  resource = $state<PromotableResource | null>(null);
  phase = $state<PromotionPhase>('idle');
  review = $state<Review | null>(null);
  error = $state<string | null>(null);
  /** The opened/updated promotion PR (GH2) — set once the bot opens it. */
  pr = $state<PullRequestRef | null>(null);
  /** Live PR/CI/deploy status (GH3) — polled from GitHub via the bot; reflects, never asserts. */
  liveStatus = $state<PromoteStatus | null>(null);
  /** The persisted Promotion id (LB3) — drives the audit-trail fetch (LB4). */
  promotionId = $state<string | null>(null);
  /** The append-only, GitHub-attributed audit trail (LB4) — accrues as the poll reconciles. */
  audit = $state<AuditEvent[]>([]);

  // --- separation of duties (SV4/GH4) ---
  /** The requester of the CURRENT promotion view (display-only): the signed-in viewer for a fresh
   * request, or the stored requester when a promotion is opened from history (so a Steward's
   * cross-user view attributes the real requester, not themselves). */
  requesterEmail = $state<string | null>(null);
  /** The signed-in viewer's OBO email (from /api/whoami) — the baseline requester for a fresh flow. */
  viewerEmail = $state<string | null>(null);
  /** The configured Steward (from /api/whoami.steward) — the distinct approver, shown in the UI. */
  steward = $state<string | null>(null);
  /** Whether the SIGNED-IN viewer IS the Steward (identity-derived from /api/whoami, not a toggle).
   * Drives whether the approval view is shown — replaces the old manual Autor/Steward switch. */
  isSteward = $state(false);

  /** The selected resource id (or '' for none) — convenient for binding a Select. */
  get selectedId(): string {
    return this.resource?.id ?? '';
  }

  /** A BLOCKER finding stands (the gate failed). */
  get hasBlocker(): boolean {
    return this.review?.gate.conclusion === 'failure';
  }

  /** Whether the CURRENT promotion was requested by the signed-in viewer (it's "mine"). */
  get isMine(): boolean {
    return !!(this.viewerEmail && this.requesterEmail &&
              this.viewerEmail.toLowerCase() === this.requesterEmail.toLowerCase());
  }

  /**
   * The approval view for the current promotion + the signed-in viewer. Identity-derived (no manual
   * toggle): if the promotion is MINE I only ever wait for the Steward (even if I'm also a Steward —
   * separation of duties); a Steward viewing SOMEONE ELSE'S promotion (opened from "Todas") gets the
   * approval affordance.
   *
   * TRUST BOUNDARY: a DISPLAY-ONLY preview on proxy-forwarded identities — the real SoD is the GitHub
   * Environment gate (required reviewer + prevent_self_review), which enforces it on GitHub identity.
   */
  get approval(): { state: ApprovalState; canApprove: boolean } {
    if (this.isMine || !this.isSteward) return { state: 'author', canApprove: false };
    if (this.hasBlocker) return { state: 'blocked', canApprove: false };
    return { state: 'ready', canApprove: true };
  }

  /** Pick a resource. A new selection invalidates any prior verdict so nothing misleads. */
  select(resource: PromotableResource | null): void {
    this.resource = resource;
    this.review = null;
    this.error = null;
    this.phase = 'idle';
    this.pr = null;
    this.liveStatus = null;
    this.promotionId = null;
    this.audit = [];
    this.requesterEmail = this.viewerEmail; // a fresh flow is requested by the viewer
  }

  /** Refresh the audit trail (LB4) for the active promotion. Best-effort: keep the last on error. */
  async refreshAudit(): Promise<void> {
    const id = this.promotionId;
    if (!id) return;
    try {
      const events = await getPromotionAudit(id);
      if (this.promotionId === id) this.audit = events;
    } catch {
      /* transient — keep the last known trail */
    }
  }

  /** Poll the live status of the open PR (bot read). Keeps the last value on a transient error. */
  async refreshStatus(): Promise<void> {
    const pr = this.pr;
    if (!pr) return;
    try {
      const s = await getPromoteStatus(pr.number);
      if (this.pr?.number === pr.number) this.liveStatus = s; // ignore if the PR changed mid-flight
    } catch {
      /* transient — keep the last known status */
    }
  }

  /**
   * Request a promotion for the selected resource: review it (OBO export + app-SP reviewer) AND
   * open/update a real GitHub PR with the attributed review comment (bot). One action, one review.
   */
  async requestPromotion(): Promise<void> {
    if (!this.resource || this.phase === 'reviewing') return; // no double-submit
    const id = this.resource.id;
    this.phase = 'reviewing';
    this.review = null;
    this.error = null;
    this.pr = null;
    this.liveStatus = null;
    const resource = this.resource;
    try {
      const res = await postPromote(resource);
      if (this.resource?.id !== id) return; // selection changed mid-flight — drop the stale result
      this.review = res.review;
      this.pr = res.pr;
      this.promotionId = res.promotion_id ?? null;
      this.phase = 'reviewed';
      void this.refreshAudit(); // show the `requested` event immediately
    } catch (e) {
      if (this.resource?.id !== id) return;
      this.error = e instanceof Error ? e.message : String(e);
      this.phase = 'error';
    }
  }

  /**
   * Recover the most-recent promotion on load (LB3): render its STORED Review Snapshot + PR ref
   * from the durable store — WITHOUT re-running the LLM reviewer (no `/api/promote` call). The live
   * PR/CI/deploy status still comes from the GitHub poll (the existing `refreshStatus` effect picks
   * up once `pr` is set). Best-effort: any failure (no store, no promotions, parse error) is a
   * silent no-op so the normal selection flow proceeds. Returns true if a promotion was restored.
   */
  async recover(): Promise<boolean> {
    if (this.phase !== 'idle' || this.resource) return false; // don't clobber an active flow
    try {
      const list = await getPromotions('mine');
      if (list.length === 0) return false;
      // Prefer the newest IN-FLIGHT promotion (so a reload mid-promotion resumes it); fall back to
      // the most recent overall so a just-finished one is still visible. (LB5 adds the full list.)
      const summary = list.find((p) => !p.terminal) ?? list[0];
      const detail = await getPromotionDetail(summary.id);
      if (!detail.review || !detail.pr) return false;
      if (this.phase !== 'idle' || this.resource) return false; // a selection raced in — yield to it
      this._apply(summary, detail);
      this.phase = 'reviewed';
      return true;
    } catch {
      return false; // store unavailable / nothing to recover — proceed with the normal flow
    }
  }

  /**
   * Open a specific promotion from the history list (LB5) — load its STORED snapshot + audit and
   * render it (no reviewer re-run). Unlike recover() this is an explicit user action, so it replaces
   * whatever is shown. The polling effect resumes the live status once `pr` is set.
   */
  async open(summary: PromotionSummary): Promise<void> {
    // Reset to a clean loading state immediately so the user never sees the PRIOR promotion's review
    // during the fetch. The id guard drops a stale result if a different row is opened mid-flight.
    this.select(null);
    this.promotionId = summary.id;
    try {
      const detail = await getPromotionDetail(summary.id);
      if (this.promotionId !== summary.id) return; // another open() raced in — drop this result
      this._apply(summary, detail);
      this.phase = detail.review ? 'reviewed' : 'idle';
    } catch (e) {
      if (this.promotionId !== summary.id) return;
      this.error = e instanceof Error ? e.message : String(e);
      this.phase = 'error';
    }
  }

  /** Shared: apply a fetched promotion detail to the reactive state (recover + open). */
  private _apply(summary: PromotionSummary, detail: PromotionDetail): void {
    this.resource = {
      id: summary.resource_id,
      title: summary.resource_title ?? summary.resource_id,
      kind: summary.resource_kind,
    };
    this.review = detail.review;
    this.pr = detail.pr;
    this.promotionId = summary.id;
    this.liveStatus = detail.live_status; // cached status -> the phase badge renders immediately
    this.audit = detail.audit ?? [];
    // Attribute the STORED requester (so an admin's cross-user view shows the real requester, and the
    // display-only SoD preview compares the steward against the right person).
    this.requesterEmail = summary.requester_email ?? this.viewerEmail;
  }
}

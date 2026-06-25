/**
 * Promotion flow state — a small reactive state machine shared across the screens.
 *
 * Using a class with `$state` fields (per Svelte best practice) lets SV2 (select + request),
 * SV3 (render the review) and SV4 (steward approval) all read/drive one source of truth.
 *
 * Phases today: idle → reviewing → reviewed (or error). The flow is intentionally modeled as a
 * machine so the FUTURE git integration drops in as new phases without reshaping callers:
 *   reviewed → requesting_pr → pr_open → approved (PR approved by steward) → deploying → deployed
 * SV4 adds the in-app approval (a faithful preview of the policy); the REAL separation-of-duties
 * enforcement is the CI/CD GitHub Environment gate (required reviewer + prevent_self_review). When
 * the git integration lands, `approve()` is replaced by "approve the open PR".
 */
import type { PromotableResource, Review } from './types';
import { postPromote, type PullRequestRef } from './api';

export type PromotionPhase = 'idle' | 'reviewing' | 'reviewed' | 'error';
export type Persona = 'author' | 'steward';

/** Why approval is/ isn't available — mirrors app_logic.can_approve + the BLOCKER gate. */
export type ApprovalState = 'author' | 'blocked' | 'sod' | 'ready' | 'approved';

export class Promotion {
  resource = $state<PromotableResource | null>(null);
  phase = $state<PromotionPhase>('idle');
  review = $state<Review | null>(null);
  error = $state<string | null>(null);
  /** The opened/updated promotion PR (GH2) — set once the bot opens it. */
  pr = $state<PullRequestRef | null>(null);

  // --- separation of duties (SV4) ---
  /** Demo persona toggle: act as the Autor (requester) or the Steward (approver). */
  persona = $state<Persona>('author');
  /** Whether the steward has approved this review (advances the approval + deploy timeline rows). */
  approved = $state(false);
  /** The OBO requester identity (from /api/whoami) — never a constant. */
  requesterEmail = $state<string | null>(null);
  /** The configured Steward (from /api/whoami.steward) — the distinct approver. */
  steward = $state<string | null>(null);

  /** The selected resource id (or '' for none) — convenient for binding a Select. */
  get selectedId(): string {
    return this.resource?.id ?? '';
  }

  /** A BLOCKER finding stands (the gate failed). */
  get hasBlocker(): boolean {
    return this.review?.gate.conclusion === 'failure';
  }

  /** Who would approve given the current persona. */
  get approver(): string | null {
    return this.persona === 'steward' ? this.steward : this.requesterEmail;
  }

  /**
   * The approval decision (mirrors can_approve + the BLOCKER gate).
   *
   * TRUST BOUNDARY: this client-side check runs on DISPLAY-ONLY identities (`requesterEmail` from
   * `x-forwarded-email`, `steward` from config) — it's a faithful PREVIEW of the policy, not
   * enforcement. The real separation of duties is the CI/CD GitHub Environment gate (required
   * reviewer + prevent_self_review). A future multi-user/prod path must add an `/api/approve`
   * endpoint that resolves the approver from the OBO TOKEN server-side and re-checks can_approve.
   */
  get approval(): { state: ApprovalState; canApprove: boolean } {
    if (this.approved) return { state: 'approved', canApprove: false };
    if (this.persona !== 'steward') return { state: 'author', canApprove: false };
    if (this.hasBlocker) return { state: 'blocked', canApprove: false };
    // SoD: the requester can never approve their own promotion (also blocks a null/misconfigured
    // steward — the safe direction).
    if (!this.approver || this.approver === this.requesterEmail) return { state: 'sod', canApprove: false };
    return { state: 'ready', canApprove: true };
  }

  // Toggling persona does NOT reset `approved`: an approval is a decision, not a view state, so it
  // persists across the demo toggle. Stale approvals across resources are cleared in select()/review.
  setPersona(p: Persona): void {
    this.persona = p;
  }

  /** Steward approval (gated by `approval.canApprove`). */
  approve(): void {
    if (this.approval.canApprove) this.approved = true;
  }

  /** Pick a resource. A new selection invalidates any prior verdict + approval so nothing misleads. */
  select(resource: PromotableResource | null): void {
    this.resource = resource;
    this.review = null;
    this.error = null;
    this.phase = 'idle';
    this.approved = false;
    this.pr = null;
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
    this.approved = false; // a fresh promotion is not yet approved
    this.pr = null;
    try {
      const res = await postPromote(id);
      if (this.resource?.id !== id) return; // selection changed mid-flight — drop the stale result
      this.review = res.review;
      this.pr = res.pr;
      this.phase = 'reviewed';
    } catch (e) {
      if (this.resource?.id !== id) return;
      this.error = e instanceof Error ? e.message : String(e);
      this.phase = 'error';
    }
  }
}

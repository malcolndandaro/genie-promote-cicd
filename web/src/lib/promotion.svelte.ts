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
import type { AudienceSpec, PromotableResource, Review } from './types';
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
  /** A restored Lakebase snapshot is useful context, but it is not presented as current until the
   * first GitHub read completes. This prevents a stale "merged" cache contradicting live prod. */
  statusFresh = $state(false);
  statusRefreshing = $state(false);
  statusError = $state<string | null>(null);
  /** Deep GitHub job/annotation evidence is intentionally lazy so the hot phase poll stays fast. */
  evidenceLoading = $state(false);
  evidenceError = $state<string | null>(null);
  evidenceLoadedRunId = $state<number | null>(null);
  /** The persisted Promotion id (LB3) — drives the audit-trail fetch (LB4). */
  promotionId = $state<string | null>(null);
  /** True when the last request found NOTHING to promote — the space is already in prod byte-
   * identical, so no PR was opened. The UI shows a "nada a promover" notice (the review still ran,
   * so `review` is populated). Reset by every `select()`/new request. */
  noChange = $state(false);
  /** Whether the CURRENT promotion flow was actively STARTED by the user on this screen this session
   * (via `requestPromotion`) — as opposed to auto-restored by `recover()` on page load. "Meus
   * espaços" gates the inline pipeline/review on this so it appears only when the user actually
   * requests a promotion, never automatically pinned to the last one at the bottom of the list. */
  initiatedHere = $state(false);
  /** A stored promotion explicitly selected from the Space/history list is being loaded. Kept
   * separate from `reviewing`: opening a snapshot must never look like the reviewer is running. */
  opening = $state(false);
  /** The append-only, GitHub-attributed audit trail (LB4) — accrues as the poll reconciles. */
  audit = $state<AuditEvent[]>([]);

  // --- identity context for the current promotion view ---
  /** The requester of the CURRENT promotion view (display-only): the signed-in viewer for a fresh
   * request, or the stored requester when a promotion is opened from history (so an admin's
   * cross-user view attributes the real requester, not themselves). */
  requesterEmail = $state<string | null>(null);
  /** The signed-in viewer's OBO email (from /api/whoami) — the baseline requester for a fresh flow. */
  viewerEmail = $state<string | null>(null);

  /** The selected resource id (or '' for none) — convenient for binding a Select. */
  get selectedId(): string {
    return this.resource?.id ?? '';
  }

  /** True from snapshot restore until GitHub has confirmed the current phase at least once. */
  get waitingForLiveStatus(): boolean {
    return !!this.pr && !this.statusFresh;
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
   * R1: SoD is GitHub's gate entirely. The app no longer tracks a Steward identity, so the
   * approval state is always 'author' (no in-app approve affordance). The PR banner shows the
   * deploy-gate URL when `awaiting_approval` — the Responsável Técnico acts on GitHub directly.
   *
   * TRUST BOUNDARY: a DISPLAY-ONLY preview on proxy-forwarded identities — the real SoD is the
   * GitHub Environment gate (required reviewer + prevent_self_review), enforced on GitHub identity.
   */
  get approval(): { state: ApprovalState; canApprove: boolean } {
    return { state: 'author', canApprove: false };
  }

  /** Incremented on every `select()` call (same-space reselection included) — `MeusEspacos`
   * keys the confirmation panel off this, not `resource?.id`, so the declaration forms
   * (AudienceSpecForm/PromotionMappingForm) always remount fresh, even for the SAME space. */
  selectionSeq = $state(0);

  /** Pick a resource. A new selection invalidates any prior verdict so nothing misleads — including
   * a RE-selection of the SAME space: a prior round's declared access/title/table-mapping must
   * never silently ride the next request (found live, PR #25 — a re-request of the same space still
   * carried the previous declaration because neither this reset nor a remount happened for the
   * same-space case). `selectionSeq` additionally forces the declaration forms to remount, which is
   * what actually re-seeds PromotionMappingForm's title/table-mapping from the fresh preview. */
  select(resource: PromotableResource | null): void {
    this.resource = resource;
    this.review = null;
    this.error = null;
    this.phase = 'idle';
    this.pr = null;
    this.liveStatus = null;
    this.statusFresh = false;
    this.statusRefreshing = false;
    this.statusError = null;
    this.evidenceLoading = false;
    this.evidenceError = null;
    this.evidenceLoadedRunId = null;
    this.promotionId = null;
    this.audit = [];
    this.requesterEmail = this.viewerEmail; // a fresh flow is requested by the viewer
    this.initiatedHere = false; // a bare selection isn't yet a requested promotion
    this.opening = false;
    this.noChange = false;
    this.pendingAudienceSpec = undefined;
    this.pendingProdTitle = undefined;
    this.pendingTableMapping = undefined;
    this.selectionSeq += 1;
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
    if (!pr || this.statusRefreshing || this.evidenceLoading) return;
    this.statusRefreshing = true;
    this.statusError = null;
    try {
      const s = await getPromoteStatus(pr.number);
      if (this.pr?.number === pr.number) {
        this._applyLiveStatus(s);
        this.statusFresh = true;
      }
    } catch (e) {
      if (this.pr?.number === pr.number) {
        this.statusError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      if (this.pr?.number === pr.number) this.statusRefreshing = false;
    }
  }

  /** Load exact provider job steps only when the operator opens support/audit details. */
  async refreshDeploymentEvidence(): Promise<void> {
    const pr = this.pr;
    const runId = this.liveStatus?.deploy.run_id ?? null;
    if (!pr || !runId || this.evidenceLoading || this.evidenceLoadedRunId === runId) return;
    this.evidenceLoading = true;
    this.evidenceError = null;
    try {
      const s = await getPromoteStatus(pr.number, true);
      if (this.pr?.number === pr.number) {
        this._applyLiveStatus(s);
        this.statusFresh = true;
        this.evidenceLoadedRunId = s.deploy.run_id ?? runId;
      }
    } catch (e) {
      if (this.pr?.number === pr.number) {
        this.evidenceError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      if (this.pr?.number === pr.number) this.evidenceLoading = false;
    }
  }

  /** Keep already-loaded deep evidence across later lean polling responses for the same run. */
  private _applyLiveStatus(next: PromoteStatus): void {
    const previous = this.liveStatus;
    const sameRun = !!previous?.deploy.run_id && previous.deploy.run_id === next.deploy.run_id;
    if (!sameRun) {
      this.evidenceLoadedRunId = null;
      this.evidenceError = null;
    }
    const steps = next.deploy.steps?.length ? next.deploy.steps : sameRun ? previous?.deploy.steps : undefined;
    const attempt = next.deploy.attempt ?? (sameRun ? previous?.deploy.attempt : undefined);
    this.liveStatus = {
      ...next,
      deploy: { ...next.deploy, steps, attempt },
      deploy_detail: next.deploy_detail ?? (sameRun ? previous?.deploy_detail : undefined),
    };
  }

  /** Required Público do Space for the current flow. Every principal derives to CAN_RUN. */
  pendingAudienceSpec = $state<AudienceSpec | undefined>(undefined);

  /** G7: the editable prod Space name for the CURRENT flow — mirrors `pendingAudienceSpec`, captured
   * by `PromotionMappingForm` before requesting the promotion. Pre-filled with the dev title;
   * undefined falls back to the resource's own title (unchanged from before G7). */
  pendingProdTitle = $state<string | undefined>(undefined);

  /** G7: the Requester's declared table de-para for the CURRENT flow (source dev ref -> desired
   * prod ref overrides) — mirrors `pendingAudienceSpec`. Only entries actually changed away from the
   * `/promote/preview` default need be present; undeclared (undefined/empty) means "use the plain
   * dev_->prod_ defaults", exactly as before G7. */
  pendingTableMapping = $state<Record<string, string> | undefined>(undefined);

  /**
   * Request a promotion for the selected resource: review it (OBO export + app-SP reviewer) AND
   * open/update a real GitHub PR with the attributed review comment (bot). One action, one review.
   */
  async requestPromotion(): Promise<void> {
    if (!this.resource || this.phase === 'reviewing') return; // no double-submit
    if (!this.pendingAudienceSpec) {
      this.error = 'Selecione ao menos uma pessoa ou grupo para o Público do Space.';
      this.phase = 'error';
      return;
    }
    const id = this.resource.id;
    this.phase = 'reviewing';
    this.initiatedHere = true; // user-initiated on this screen → its inline pipeline may show now
    this.noChange = false;
    this.review = null;
    this.error = null;
    this.pr = null;
    this.liveStatus = null;
    this.statusFresh = false;
    this.statusRefreshing = false;
    this.statusError = null;
    this.evidenceLoading = false;
    this.evidenceError = null;
    this.evidenceLoadedRunId = null;
    const resource = this.resource;
    try {
      const res = await postPromote(
        resource,
        this.pendingAudienceSpec,
        this.pendingProdTitle,
        this.pendingTableMapping
      );
      if (this.resource?.id !== id) return; // selection changed mid-flight — drop the stale result
      this.review = res.review;
      this.pr = res.pr;
      this.promotionId = res.promotion_id ?? null;
      this.noChange = !!res.no_change; // nothing to promote — space already in prod byte-identical
      this.phase = 'reviewed';
      void this.refreshAudit(); // show the `requested` event immediately (no-op when no PR)
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
      // `getPromotions` is newest-first. Always restore its first item: an older row whose cached
      // terminal flag lagged GitHub must never outrank a newer promotion of the same Space.
      const summary = list[0];
      const detail = await getPromotionDetail(summary.id);
      if (!detail.review || !detail.pr) return false;
      if (this.phase !== 'idle' || this.resource) return false; // a selection raced in — yield to it
      this._apply(summary, detail);
      this.phase = 'reviewed';
      this.initiatedHere = true; // surface the latest promotion as the page's initial context
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
    this.opening = true;
    try {
      const detail = await getPromotionDetail(summary.id);
      if (this.promotionId !== summary.id) return; // another open() raced in — drop this result
      this._apply(summary, detail);
      this.phase = detail.review ? 'reviewed' : 'idle';
      this.initiatedHere = true; // explicit selection: render this snapshot in Meus Espaços
    } catch (e) {
      if (this.promotionId !== summary.id) return;
      this.error = e instanceof Error ? e.message : String(e);
      this.phase = 'error';
      this.initiatedHere = true;
    } finally {
      if (this.promotionId === summary.id) this.opening = false;
    }
  }

  /**
   * Open a promotion by id (the `#/promocoes/:id` deep-link) — fetch its STORED detail + render it
   * WITHOUT re-running the reviewer. Like `open()` but starting from just an id (no pre-fetched
   * summary); uses the detail's own promotion record as the summary. Idempotent for the same id so a
   * re-render of the route doesn't reload. The polling effect resumes the live status once `pr` is set.
   */
  async openById(id: string): Promise<void> {
    if (this.promotionId === id && this.phase !== 'idle') return; // already showing this one
    this.select(null);
    this.promotionId = id;
    try {
      const detail = await getPromotionDetail(id);
      if (this.promotionId !== id) return; // another open raced in — drop this result
      this._apply(detail.promotion, detail);
      this.phase = detail.review ? 'reviewed' : 'idle';
    } catch (e) {
      if (this.promotionId !== id) return;
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
    this.liveStatus = detail.live_status; // retained as context, but hidden as current until refreshed
    this.statusFresh = false; // cached is context only; the GitHub poll makes it authoritative
    this.statusRefreshing = false;
    this.statusError = null;
    this.evidenceLoading = false;
    this.evidenceError = null;
    this.evidenceLoadedRunId = detail.live_status?.deploy.steps?.length
      ? detail.live_status.deploy.run_id ?? null
      : null;
    this.audit = detail.audit ?? [];
    // Attribute the STORED requester (so an admin's cross-user view shows the real requester).
    this.requesterEmail = summary.requester_email ?? this.viewerEmail;
  }
}

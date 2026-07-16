"""reconcile — the single function that turns observed GitHub state into a durable, attributed audit
trail + a fresh status cache (LB4). The ONLY writer of GitHub-sourced audit events; idempotent.

`reconcile(store, promotion, status, github_factory)` diffs the live `get_status` against the
Promotion's already-recorded events and appends any NEWLY-reached milestones — with GitHub-sourced
identities (`actor_github_login`) + timestamps (`github_event_at`) where GitHub exposes them — then
updates the Promotion's `current_phase` + cached `live_status` + `last_reconciled_at` + `terminal`.

Reflect, never assert (ADR-0005): it records what GitHub reports, never decides a transition GitHub
didn't. Idempotent: re-running with no change appends nothing. Backfill: a multi-step jump
(e.g. awaiting_approval → deployed, when no one was watching) emits each missing milestone at once —
which is exactly what the LB6 scheduler relies on. The `github_factory` is called lazily (only when a
`merged`/`pr_review_approved` event needs its identity), so the hot 5s poll pays nothing extra.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from promotion_store import TERMINAL_PHASES, PromotionStore

# Canonical order of GitHub-observed milestones (app-side `requested`/`re_reviewed` are written by the
# promote endpoint, not here). `failed` is the terminal alternative to `deployed`.
MILESTONE_ORDER = ("pr_opened", "pr_review_approved", "merged", "deploy_approved",
                   "deployed", "failed", "closed")
_NEED_FACTS = {"merged", "pr_review_approved"}


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse a GitHub ISO-8601 timestamp (…Z) to an aware datetime; None if absent/unparseable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def _reached(status: dict) -> set[str]:
    """The set of milestones the live status implies have been reached (cumulative — used to backfill
    a multi-step jump). Reflects GitHub; asserts nothing beyond what the status reports."""
    reached: set[str] = set()
    if status.get("pr_state"):  # the PR exists -> opened (the bot opened it)
        reached.add("pr_opened")
    # Only on a real GitHub approval (don't fabricate it from `merged` alone — a repo that allows
    # merging without a required review would otherwise get a phantom approval with no actor).
    if status.get("review_decision") == "approved":
        reached.add("pr_review_approved")
    if status.get("merged"):
        reached.add("merged")
    deploy = status.get("deploy") or {}
    # deploy_approved (the gate was RELEASED) is recorded only once the deploy run has COMPLETED — at
    # that point get_status carries the approver (the Steward), so the most governance-critical
    # identity is captured rather than recorded prematurely as null and never backfilled.
    if deploy.get("status") == "completed":
        reached.add("deploy_approved")
    phase = status.get("phase")
    if phase == "deployed":
        reached.add("deployed")
    elif phase == "deploy_failed":
        reached.add("failed")
    elif phase == "closed":  # PR abandoned (closed unmerged) — a terminal outcome worth recording
        reached.add("closed")
    return reached


def _identity(event: str, status: dict, facts: dict) -> tuple[Optional[str], Optional[datetime]]:
    """The authoritative GitHub identity + timestamp for a milestone, where GitHub exposes it.
    pr_opened/deployed/failed are bot/SP/system actions with no distinct human actor (None)."""
    if event == "pr_review_approved":
        return facts.get("review_approver"), _parse_ts(facts.get("review_approved_at"))
    if event == "merged":
        return facts.get("merged_by"), _parse_ts(facts.get("merged_at"))
    if event == "deploy_approved":
        return (status.get("deploy") or {}).get("approver"), None  # the Steward who released the gate
    return None, None


def reconcile(store: PromotionStore, promotion, status: dict,
              github_factory: Callable[[], object]) -> list[str]:
    """Append any newly-reached audit events (idempotent) + refresh the status cache. Returns the
    list of event types appended this run (empty when nothing changed)."""
    existing = {e.event_type for e in store.list_audit_events(promotion.id)}
    deployment = status.get("deployment") or status.get("deploy") or {}
    attempt = deployment.get("attempt")
    if attempt:
        # GitHub annotations are the source of truth. Lakebase only mirrors the latest evidence;
        # it never advances a stage or asserts success on its own.
        store.upsert_deployment_attempt(
            promotion.id, attempt, provider=status.get("provider") or "github")
    reached = _reached(status)
    to_append = [m for m in MILESTONE_ORDER if m in reached and m not in existing]

    facts: dict = {}
    if any(m in _NEED_FACTS for m in to_append):
        # Cold path: only fetched on a real transition that needs a human identity.
        try:
            facts = github_factory().audit_facts(promotion.pr_number)
        except Exception:  # noqa: BLE001 — never let an enrichment hiccup drop the audit event
            facts = {}

    for event in to_append:
        login, at = _identity(event, status, facts)
        revisions = status.get("revisions") or {
            "content_revision": promotion.content_revision,
            "engine_revision": promotion.engine_revision,
        }
        detail = {"phase": status.get("phase"), "observed_at": _now_iso(),
                  "revisions": revisions}
        if event in {"deployed", "failed"} and attempt:
            detail["deployment_attempt"] = attempt
        store.append_audit_event(
            promotion.id, event, actor_github_login=login, github_event_at=at,
            detail=detail)

    # Only write the cache on an ACTUAL change (new events or a phase shift) — not on every steady
    # poll. This keeps `updated_at` meaningful (it tracks real activity, not the 5s ticker) and
    # avoids a per-poll write to Lakebase for every viewer of every in-flight promotion.
    if to_append or status.get("phase") != promotion.current_phase:
        store.update_cache(
            promotion.id, current_phase=status.get("phase"), live_status=status,
            terminal=status.get("phase") in TERMINAL_PHASES)
    return to_append


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reconcile_all(store: PromotionStore, github_factory: Callable[[], object],
                  logger=None) -> dict:
    """Reconcile EVERY non-terminal promotion against live GitHub (LB6) — the scheduled-reconciler
    entrypoint, so an overnight merge/deploy is recorded even when no browser is open. Reuses the
    SAME `reconcile` (no second code path), so it's idempotent + backfills exactly like the on-read
    path. Builds the bot ONCE for the whole sweep (the per-promotion get_status reads reuse it).
    Bounded: only non-terminal promotions; a per-promotion error is logged + skipped, never aborts
    the sweep. Returns {checked, transitioned:[{id, events}]}."""
    promotions = store.list_non_terminal()
    if not promotions:
        return {"checked": 0, "transitioned": []}
    if logger and len(promotions) > 20:
        # ~3 GitHub calls each per sweep — flag when the non-terminal set is large enough to pressure
        # the installation-token budget (shared with viewer polls), so an operator can react.
        logger.warning("scheduled reconcile sweeping %s non-terminal promotions — watch the GitHub "
                       "API budget", len(promotions))
    gh = github_factory()          # one bot client for the sweep (its token caches internally)
    factory = lambda: gh           # reconcile's lazy enrichment reuses the same client  # noqa: E731

    checked = 0
    transitioned: list[dict] = []
    for p in promotions:
        if p.pr_number is None:
            continue
        try:
            expected = ({
                "content_revision": p.content_revision,
                "engine_revision": p.engine_revision,
            } if p.content_revision and p.engine_revision else None)
            status = gh.get_status(p.pr_number, approved_revisions=expected)
            appended = reconcile(store, p, status, factory)
            checked += 1
            if appended:
                transitioned.append({"id": p.id, "pr_number": p.pr_number, "events": appended})
        except Exception:  # noqa: BLE001 — one bad promotion must not abort the whole sweep
            if logger:
                logger.exception("scheduled reconcile failed for PR #%s", p.pr_number)
    return {"checked": checked, "transitioned": transitioned}

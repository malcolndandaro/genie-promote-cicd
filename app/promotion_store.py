"""promotion_store — the durable index + append-only audit log + status cache for promotions.

The ONLY owner of SQL in the app (ADR-0005). A `PromotionStore` holds all domain logic (ids,
timestamps, seq assignment, JSON shapes) and delegates raw row CRUD to an injectable **backend**:

  - `PgBackend` — Lakebase (Databricks Postgres) via psycopg + a small OAuth-refreshing pool. The
    app connects as its SERVICE PRINCIPAL using a short-lived OAuth token (no static password); the
    instance name is recovered from the platform-injected `PGHOST` so any `lakebase_instance` works.
  - `InMemoryBackend` — a faithful in-memory implementation of the SAME narrow backend interface, so
    the store's contract (1:1-PR, append-only audit, ordering, status cache) is unit-tested OFFLINE
    with NO live Lakebase (mirrors the engine's injectable-fake pattern). psycopg is imported lazily
    inside `PgBackend` so the offline suite + CI need no Postgres driver.

Lakebase is a HARD dependency of the deployed app: `build_store_from_env` fails fast + loud
(`LakebaseUnavailable`) if `PGHOST` is set but the DB can't be reached/migrated — no silent
half-broken app. Locally/in tests (no `PGHOST`) it returns None so the app still runs without a DB.

GitHub stays the source of truth (ADR-0005): this store records/recovers/caches + is reconciled
FROM GitHub (LB4). It never decides a verdict. Governance attribution lives in `audit_events`:
`actor_github_login` is authoritative; `actor_app_email` is display-only (the OBO email).

`rehydrate_events` (F1 follow-up, stakeholder decision): a prod->dev rehydrate (`app/rehydrate.py`)
must be auditable for ANY prod Space the caller can access, not only ones that went through this
app's own promotion flow — the prod store starts EMPTY (ADR-0006) so most prod Spaces have no
Promotion row at all. Rather than FK an audit row to a Promotion that may not exist, a rehydrate
with no linkable source Promotion is recorded in this STANDALONE table instead — same reasoning
`access_request_store.py`/`rules_store.py` already document for their own audit tables, kept here
(rather than as a sibling module) because `rehydrate.py`/the engine already thread a single
`PromotionStore` end-to-end and this is one flat append-only table, not a new domain with its own
pool. When a source Promotion DOES exist, the richer `audit_events`-linked `rehydrated` event (see
`EVENT_TYPES` above) is used instead — see `rehydrate.py`'s `_audit`.
"""
from __future__ import annotations

import dataclasses
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Protocol

# --- domain model -----------------------------------------------------------

# The lifecycle events appended to a promotion's audit trail. `requested`/`re_reviewed` are written
# by the app on a promote request; the rest are appended by reconcile (LB4) from observed GitHub
# state. Append-only — there is no update/delete path for an event.
EVENT_TYPES = (
    "requested", "re_reviewed", "pr_opened", "pr_review_approved",
    "merged", "deploy_approved", "deployed", "failed", "closed",
    # A3/F1: prod->dev rehydrate is app-written (no git PR involved) and may legitimately recur
    # (reseed dev again after another wipe) — see RECURRING_EVENTS below.
    "rehydrated",
)
# App-written events that may legitimately RECUR (so they're excluded from the per-type dedup that
# protects reconcile's GitHub-observed milestones from a concurrent double-append).
RECURRING_EVENTS = ("requested", "re_reviewed", "rehydrated")

# Phases that mean the promotion is over (the LB6 scheduler stops visiting these). `deploy_failed` is
# terminal so a failed deploy doesn't loop the scheduler forever — a fix + re-merge is a new Promotion
# (new PR); a manual re-run is still picked up by an open viewer's poll.
TERMINAL_PHASES = ("deployed", "deploy_failed", "closed")


@dataclasses.dataclass
class Promotion:
    id: str
    resource_id: str
    resource_kind: str
    resource_title: Optional[str]
    requester_email: Optional[str]   # OBO email — DISPLAY ONLY (governance uses GitHub identities)
    pr_number: Optional[int]
    pr_url: Optional[str]
    branch: Optional[str]
    current_phase: Optional[str]
    live_status: Optional[dict]      # cached get_status (jsonb) — renders immediately on load (LB4)
    terminal: bool
    last_reconciled_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    # F2: the Requester's declared AccessSpec (jsonb; `access_spec.AccessSpec.to_dict()` shape) —
    # persisted WITH the Promotion so the Review/history view shows exactly what was declared, even
    # after the sidecar's own PR/branch is long merged/closed. None when no AccessSpec was declared
    # (pre-F2 promotions, or a promotion with no declared access).
    access_spec: Optional[dict] = None
    # Pilot Público do Space desired set. New promotions write this field; `access_spec` above is
    # read-only compatibility until ADR-0009 Phase 2 drops it.
    audience_spec: Optional[dict] = None
    # G7: the Requester's declared table de-para (jsonb; source DEV ref -> desired prod ref
    # overrides) — persisted WITH the Promotion for the SAME reason as access_spec above (survives
    # independently of the `.mapping.json` sidecar's own PR/branch lifetime). None/empty means the
    # plain dev_->prod_ rebind, no overrides.
    table_mapping: Optional[dict] = None
    # Provider-neutral Change Request identity + immutable approved pair (ADR-0008).
    change_provider: Optional[str] = None
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    content_revision: Optional[str] = None
    engine_revision: Optional[str] = None


@dataclasses.dataclass
class ReviewSnapshot:
    id: str
    promotion_id: str
    created_at: datetime
    gate_conclusion: Optional[str]
    gate_summary: Optional[str]
    findings: list           # jsonb
    eval: Optional[dict]     # jsonb
    timeline: list           # jsonb


@dataclasses.dataclass
class AuditEvent:
    id: str
    promotion_id: str
    seq: int
    occurred_at: datetime
    event_type: str
    actor_github_login: Optional[str]   # AUTHORITATIVE governance identity (from GitHub)
    actor_app_email: Optional[str]      # display-only (OBO email); set only on `requested`
    github_event_at: Optional[datetime]
    detail: Optional[dict]              # jsonb


@dataclasses.dataclass
class RehydrateEvent:
    """A prod->dev rehydrate with NO linkable source Promotion (see the module docstring) — its own
    flat, append-only row: no `seq` (nothing to order WITHIN — there's no parent to scope it to)
    and deliberately NO FK to `promotions(id)`, mirroring `rules_store.rule_audit_events`."""
    id: str
    resource_id: str                  # the prod Space id that was rehydrated FROM
    resource_title: Optional[str]
    actor_email: str                  # the live-checked ACTING identity — never a header
    mode: str                         # "create" | "overwrite"
    dev_space_id: Optional[str]
    detail: Optional[dict]            # jsonb — same shape as the audit_events "rehydrated" detail
    created_at: datetime


@dataclasses.dataclass
class DeploymentAttempt:
    """Canonical deployment evidence mirrored from the provider's live workflow annotations."""
    id: str
    promotion_id: str
    provider: str
    external_run_id: str
    run_attempt: int
    revisions: dict
    mutation_started: bool
    completed_stages: list
    current_stage: Optional[str]
    failed_stage: Optional[str]
    target_ids: dict
    reason: Optional[str]
    run_url: Optional[str]
    terminal_state: str
    observed_at: datetime
    updated_at: datetime


class LakebaseUnavailable(RuntimeError):
    """Raised at startup when Lakebase is a hard dependency but unreachable/misconfigured."""


class DuplicatePRNumber(ValueError):
    """Raised when creating a second Promotion for a PR that already has one (the 1:1-PR rule).

    A `ValueError` subclass so existing handlers keep working; both backends raise THIS type (the
    in-memory fake AND the Postgres `UNIQUE(pr_number)` violation) so a caller (LB3) catches one
    exception regardless of backend — no backend-specific error leaks as a 500."""


# --- backend interface ------------------------------------------------------


class StoreBackend(Protocol):
    """Raw row persistence (dicts in/out). All domain logic lives in PromotionStore; a backend only
    stores/fetches rows + assigns the per-promotion audit `seq` atomically + enforces append-only by
    exposing NO update/delete for snapshots or events."""

    def migrate(self) -> None: ...
    def insert_promotion(self, row: dict) -> None: ...
    def get_promotion(self, promotion_id: str) -> Optional[dict]: ...
    def find_promotion_by_pr(self, pr_number: int) -> Optional[dict]: ...
    def list_promotions(self, requester_email: Optional[str]) -> list[dict]: ...  # newest first
    def list_non_terminal(self) -> list[dict]: ...  # all non-terminal (for the scheduler)
    def update_promotion(self, promotion_id: str, fields: dict) -> None: ...
    def insert_snapshot(self, row: dict) -> None: ...
    def list_snapshots(self, promotion_id: str) -> list[dict]: ...  # oldest -> newest
    def insert_audit_event(self, row: dict) -> Optional[int]: ...  # seq, or None if a dup milestone
    def list_audit_events(self, promotion_id: str) -> list[dict]: ...  # ordered by seq
    # Cross-Promotion audit (F4): all events joined with resource_id/resource_title, newest-first,
    # bounded — ONE query (no per-Promotion N+1). S4 (app-ux-overhaul): added offset (simple
    # pagination — this app's audit volume doesn't warrant keyset/cursor pagination), a date
    # range, and space/actor filters, all optional and independently combinable.
    def list_recent_audit_events(
        self, limit: int, *, offset: int = 0,
        resource_id: Optional[str] = None, actor: Optional[str] = None,
        after: Optional[datetime] = None, before: Optional[datetime] = None,
    ) -> list[dict]: ...
    # Standalone rehydrate audit (F1 follow-up) — NO FK to promotions, see RehydrateEvent.
    def insert_rehydrate_event(self, row: dict) -> None: ...
    def list_rehydrate_events(self, resource_id: Optional[str], limit: int) -> list[dict]: ...  # newest first
    def upsert_deployment_attempt(self, row: dict) -> None: ...
    def list_deployment_attempts(self, promotion_id: str) -> list[dict]: ...
    def healthcheck(self) -> None: ...
    def close(self) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- the store (all domain logic; backend-agnostic) -------------------------


class PromotionStore:
    def __init__(self, backend: StoreBackend, *, clock: Callable[[], datetime] = _now):
        self._b = backend
        self._clock = clock

    def migrate(self) -> None:
        self._b.migrate()

    def close(self) -> None:
        """Release the backend's resources (e.g. the Pg connection pool) on app shutdown."""
        self._b.close()

    # -- promotions ---------------------------------------------------------
    def create_promotion(self, *, resource_id: str, resource_kind: str,
                         resource_title: Optional[str], requester_email: Optional[str],
                         pr_number: Optional[int], pr_url: Optional[str], branch: Optional[str],
                         current_phase: Optional[str], live_status: Optional[dict],
                         access_spec: Optional[dict] = None,
                         audience_spec: Optional[dict] = None,
                         table_mapping: Optional[dict] = None,
                         change_provider: Optional[str] = None,
                         external_id: Optional[str] = None,
                         external_url: Optional[str] = None,
                         content_revision: Optional[str] = None,
                         engine_revision: Optional[str] = None) -> Promotion:
        now = self._clock()
        p = Promotion(
            id=str(uuid.uuid4()), resource_id=resource_id, resource_kind=resource_kind,
            resource_title=resource_title, requester_email=requester_email, pr_number=pr_number,
            pr_url=pr_url, branch=branch, current_phase=current_phase, live_status=live_status,
            terminal=False, last_reconciled_at=None, created_at=now, updated_at=now,
            access_spec=access_spec, audience_spec=audience_spec, table_mapping=table_mapping,
            change_provider=change_provider, external_id=external_id, external_url=external_url,
            content_revision=content_revision, engine_revision=engine_revision)
        self._b.insert_promotion(dataclasses.asdict(p))
        return p

    def touch(self, promotion_id: str) -> None:
        """Bump `updated_at` (e.g. on a re-review that adds a snapshot but no cache change), so
        freshness/ordering reflect the activity even before the next reconcile."""
        self._b.update_promotion(promotion_id, {"updated_at": self._clock()})

    def update_declarations(self, promotion_id: str, *, resource_title: Optional[str],
                            access_spec: Optional[dict] = None,
                            audience_spec: Optional[dict] = None,
                            table_mapping: Optional[dict] = None) -> None:
        """Refresh the declared `resource_title`/`access_spec`/`table_mapping` on an EXISTING
        Promotion (found #3): a re-request on the same open PR can change any of these three (a
        different prod name, a revised AccessSpec, a new table de-para), but `create_promotion`
        only sets them ONCE, at creation — without this, the stored Promotion kept showing the
        FIRST request's declarations forever, stale against what the PR/sidecars actually now
        carry. Called instead of (not in addition to) `touch()` on a re-request — it bumps
        `updated_at` itself, same as `touch`/`update_cache` do. The new values REPLACE the old ones
        outright (declare-latest-wins, not a merge): a re-request that drops an AccessSpec (e.g. the
        Requester decided no principal is needed after all) must show that, not keep echoing a
        stale one only the previous round declared."""
        self._b.update_promotion(promotion_id, {
            "resource_title": resource_title, "access_spec": access_spec,
            "audience_spec": audience_spec,
            "table_mapping": table_mapping, "updated_at": self._clock()})

    def get_promotion(self, promotion_id: str) -> Optional[Promotion]:
        row = self._b.get_promotion(promotion_id)
        return _as(Promotion, row)

    def find_by_pr(self, pr_number: int) -> Optional[Promotion]:
        return _as(Promotion, self._b.find_promotion_by_pr(pr_number))

    def find_by_change_request(self, provider: str, external_id: str) -> Optional[Promotion]:
        """Canonical lookup with a PR-number fallback for rows created before ADR-0008."""
        match = next(
            (p for p in self.list_promotions(None)
             if p.change_provider == provider and p.external_id == external_id),
            None,
        )
        if match is not None:
            return match
        if provider == "github" and external_id.isdigit():
            return self.find_by_pr(int(external_id))
        return None

    def update_change_request(self, promotion_id: str, *, provider: str, external_id: str,
                              external_url: Optional[str], content_revision: Optional[str],
                              engine_revision: Optional[str]) -> None:
        self._b.update_promotion(promotion_id, {
            "change_provider": provider,
            "external_id": external_id,
            "external_url": external_url,
            "content_revision": content_revision,
            "engine_revision": engine_revision,
            "updated_at": self._clock(),
        })

    def list_promotions(self, requester_email: Optional[str] = None) -> list[Promotion]:
        """Promotions, newest first. `requester_email=None` => all (the caller enforces the role)."""
        return [_as(Promotion, r) for r in self._b.list_promotions(requester_email)]

    def list_non_terminal(self) -> list[Promotion]:
        """All non-terminal promotions (for the scheduled reconciler, LB6) — the ones still worth
        polling GitHub for. Terminal ones (deployed/closed/deploy_failed) are skipped."""
        return [_as(Promotion, r) for r in self._b.list_non_terminal()]

    def update_cache(self, promotion_id: str, *, current_phase: Optional[str],
                     live_status: Optional[dict], terminal: bool,
                     last_reconciled_at: Optional[datetime] = None) -> None:
        """Refresh the cached status from a reconcile (LB4). Never an audit write — that is separate
        and append-only."""
        t = last_reconciled_at or self._clock()  # one clock read so the two timestamps agree
        self._b.update_promotion(promotion_id, {
            "current_phase": current_phase, "live_status": live_status, "terminal": terminal,
            "last_reconciled_at": t, "updated_at": t})

    # -- review snapshots (immutable; many per promotion) -------------------
    def append_snapshot(self, promotion_id: str, *, gate_conclusion: Optional[str],
                        gate_summary: Optional[str], findings: list, eval: Optional[dict],
                        timeline: list) -> ReviewSnapshot:
        s = ReviewSnapshot(
            id=str(uuid.uuid4()), promotion_id=promotion_id, created_at=self._clock(),
            gate_conclusion=gate_conclusion, gate_summary=gate_summary,
            findings=findings or [], eval=eval, timeline=timeline or [])
        self._b.insert_snapshot(dataclasses.asdict(s))
        return s

    def list_snapshots(self, promotion_id: str) -> list[ReviewSnapshot]:
        return [_as(ReviewSnapshot, r) for r in self._b.list_snapshots(promotion_id)]

    def latest_snapshot(self, promotion_id: str) -> Optional[ReviewSnapshot]:
        snaps = self.list_snapshots(promotion_id)
        return snaps[-1] if snaps else None

    # -- audit trail (append-only) ------------------------------------------
    def append_audit_event(self, promotion_id: str, event_type: str, *,
                          actor_github_login: Optional[str] = None,
                          actor_app_email: Optional[str] = None,
                          github_event_at: Optional[datetime] = None,
                          detail: Optional[dict] = None) -> Optional[AuditEvent]:
        """Append an audit event. Returns None if it was a reconcile MILESTONE already recorded (the
        idempotency safety net for a concurrent double-reconcile) — the caller can ignore it."""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}")
        row = {
            "id": str(uuid.uuid4()), "promotion_id": promotion_id, "occurred_at": self._clock(),
            "event_type": event_type, "actor_github_login": actor_github_login,
            "actor_app_email": actor_app_email, "github_event_at": github_event_at, "detail": detail,
        }
        seq = self._b.insert_audit_event(row)  # backend assigns the per-promotion seq atomically
        return AuditEvent(seq=seq, **row) if seq is not None else None

    def list_audit_events(self, promotion_id: str) -> list[AuditEvent]:
        return [_as(AuditEvent, r) for r in self._b.list_audit_events(promotion_id)]

    def list_recent_audit_events(
        self, limit: int = 200, *, offset: int = 0,
        resource_id: Optional[str] = None, actor: Optional[str] = None,
        after: Optional[datetime] = None, before: Optional[datetime] = None,
    ) -> list[dict]:
        """Cross-Promotion audit (F4): every Promotion's audit events, newest-first, each joined with
        its Promotion's `resource_id`/`resource_title`, in ONE backend query (replaces the per-
        Promotion N+1). Returns plain dicts (not `AuditEvent`) because the resource fields live on
        the Promotion, not the event.

        S4 (app-ux-overhaul, GR4): `offset` pages past `limit` (simple, not keyset — this app's
        audit volume doesn't need cursor pagination); `resource_id`/`actor` filter to one space /
        one actor (actor matches EITHER `actor_github_login` or `actor_app_email` — the standalone
        Audit page reuses `AuditTrail.svelte`'s existing actor-display convention, so filtering
        should match whichever identity the UI is showing); `after`/`before` bound the date range.
        All filters are optional and independently combinable."""
        return self._b.list_recent_audit_events(
            limit, offset=offset, resource_id=resource_id, actor=actor, after=after, before=before)

    # -- standalone rehydrate audit (no source Promotion; see RehydrateEvent) ----
    def append_rehydrate_event(self, *, resource_id: str, resource_title: Optional[str],
                               actor_email: str, mode: str, dev_space_id: Optional[str],
                               detail: Optional[dict] = None) -> RehydrateEvent:
        """Record a rehydrate that has NO source Promotion to attach to (F1 follow-up, stakeholder
        decision: rehydrate must work for any prod Space the caller can access). Deliberately NOT
        `append_audit_event` — that call FKs to `promotions(id)`, which doesn't exist here."""
        if not actor_email:
            raise ValueError("actor_email is required (verified identity, never a header)")
        if mode not in ("create", "overwrite"):
            raise ValueError(f"mode must be 'create' or 'overwrite', got {mode!r}")
        e = RehydrateEvent(
            id=str(uuid.uuid4()), resource_id=resource_id, resource_title=resource_title,
            actor_email=actor_email, mode=mode, dev_space_id=dev_space_id, detail=detail,
            created_at=self._clock())
        self._b.insert_rehydrate_event(dataclasses.asdict(e))
        return e

    def list_rehydrate_events(self, resource_id: Optional[str] = None,
                              limit: int = 200) -> list[RehydrateEvent]:
        return [_as(RehydrateEvent, r) for r in self._b.list_rehydrate_events(resource_id, limit)]

    # -- deployment attempts (provider-observed, mutable mirror of latest stage evidence) --------
    def upsert_deployment_attempt(self, promotion_id: str, evidence: dict,
                                  *, provider: str = "github") -> DeploymentAttempt:
        """Mirror one canonical provider Attempt. This never decides success; callers pass only
        evidence observed from the provider's workflow annotations."""
        attempt_id = str(evidence.get("attempt_id") or "")
        if not attempt_id:
            raise ValueError("deployment attempt_id is required")
        terminal_state = str(evidence.get("terminal_state") or "running")
        if terminal_state not in {"running", "operational_failed", "partial_failed", "succeeded"}:
            raise ValueError(f"unknown deployment terminal_state {terminal_state!r}")
        now = self._clock()
        row = dataclasses.asdict(DeploymentAttempt(
            id=attempt_id, promotion_id=promotion_id, provider=provider,
            external_run_id=attempt_id.split(":")[-2] if ":" in attempt_id else attempt_id,
            run_attempt=int(evidence.get("run_attempt") or 1),
            revisions=dict(evidence.get("revisions") or {}),
            mutation_started=bool(evidence.get("mutation_started")),
            completed_stages=list(evidence.get("completed_stages") or []),
            current_stage=evidence.get("current_stage"), failed_stage=evidence.get("failed_stage"),
            target_ids=dict(evidence.get("target_ids") or {}), reason=evidence.get("reason"),
            run_url=evidence.get("run_url"), terminal_state=terminal_state,
            observed_at=now, updated_at=now,
        ))
        self._b.upsert_deployment_attempt(row)
        return _as(DeploymentAttempt, row)

    def list_deployment_attempts(self, promotion_id: str) -> list[DeploymentAttempt]:
        return [_as(DeploymentAttempt, row)
                for row in self._b.list_deployment_attempts(promotion_id)]


def _as(cls, row: Optional[dict]):
    """Build a dataclass from a backend row dict (only the dataclass's own fields)."""
    if row is None:
        return None
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: row[k] for k in names})


# --- in-memory backend (offline tests; no psycopg) --------------------------


class InMemoryBackend:
    """A faithful in-memory backend for offline tests — enforces 1:1-PR, append-only, and ordering,
    so the store's external contract is verified with no live Lakebase (the PRD's 'lightweight
    fake')."""

    def __init__(self):
        self._promotions: dict[str, dict] = {}
        self._snapshots: list[dict] = []
        self._events: list[dict] = []
        self._rehydrate_events: list[dict] = []
        self._deployment_attempts: dict[str, dict] = {}
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def insert_promotion(self, row: dict) -> None:
        pr = row.get("pr_number")
        if pr is not None and any(p.get("pr_number") == pr for p in self._promotions.values()):
            raise DuplicatePRNumber(f"promotion already exists for PR #{pr}")  # mirrors UNIQUE(pr_number)
        provider, external_id = row.get("change_provider"), row.get("external_id")
        if provider and external_id and any(
            p.get("change_provider") == provider and p.get("external_id") == external_id
            for p in self._promotions.values()
        ):
            raise DuplicatePRNumber(
                f"promotion already exists for {provider} Change Request {external_id}"
            )
        self._promotions[row["id"]] = dict(row)

    def _require_promotion(self, promotion_id: str) -> None:
        # Mirror the Postgres FK (REFERENCES promotions(id)) so an invalid id fails in CI too.
        if promotion_id not in self._promotions:
            raise ValueError(f"no promotion {promotion_id!r} (FK)")

    def get_promotion(self, promotion_id: str) -> Optional[dict]:
        row = self._promotions.get(promotion_id)
        return dict(row) if row else None

    def find_promotion_by_pr(self, pr_number: int) -> Optional[dict]:
        for row in self._promotions.values():
            if row.get("pr_number") == pr_number:
                return dict(row)
        return None

    def list_promotions(self, requester_email: Optional[str]) -> list[dict]:
        rows = [dict(r) for r in self._promotions.values()
                if requester_email is None or r.get("requester_email") == requester_email]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)  # newest first

    def list_non_terminal(self) -> list[dict]:
        return [dict(r) for r in self._promotions.values() if not r.get("terminal")]

    def update_promotion(self, promotion_id: str, fields: dict) -> None:
        if promotion_id in self._promotions:
            self._promotions[promotion_id].update(fields)

    def insert_snapshot(self, row: dict) -> None:
        self._require_promotion(row["promotion_id"])
        self._snapshots.append(dict(row))

    def list_snapshots(self, promotion_id: str) -> list[dict]:
        rows = [dict(r) for r in self._snapshots if r["promotion_id"] == promotion_id]
        return sorted(rows, key=lambda r: r["created_at"])  # oldest -> newest

    def insert_audit_event(self, row: dict) -> Optional[int]:
        self._require_promotion(row["promotion_id"])
        et = row["event_type"]
        if et not in RECURRING_EVENTS and any(
                e["promotion_id"] == row["promotion_id"] and e["event_type"] == et
                for e in self._events):
            return None  # milestone already recorded — skip (mirrors the partial unique index)
        seq = 1 + max((e["seq"] for e in self._events if e["promotion_id"] == row["promotion_id"]),
                      default=0)
        self._events.append({**row, "seq": seq})
        return seq

    def list_audit_events(self, promotion_id: str) -> list[dict]:
        rows = [dict(e) for e in self._events if e["promotion_id"] == promotion_id]
        return sorted(rows, key=lambda e: e["seq"])

    def list_recent_audit_events(
        self, limit: int, *, offset: int = 0,
        resource_id: Optional[str] = None, actor: Optional[str] = None,
        after: Optional[datetime] = None, before: Optional[datetime] = None,
    ) -> list[dict]:
        out = []
        for e in self._events:
            p = self._promotions.get(e["promotion_id"], {})
            out.append({**e, "resource_id": p.get("resource_id"),
                        "resource_title": p.get("resource_title")})
        if resource_id is not None:
            out = [e for e in out if e["resource_id"] == resource_id]
        if actor is not None:
            out = [e for e in out if e.get("actor_github_login") == actor or e.get("actor_app_email") == actor]
        if after is not None:
            out = [e for e in out if e["occurred_at"] >= after]
        if before is not None:
            out = [e for e in out if e["occurred_at"] <= before]
        # newest-first; (promotion_id, seq) are the stable tiebreakers so equal timestamps (a fixed
        # test clock) still order deterministically.
        out.sort(key=lambda e: (e["occurred_at"], e["promotion_id"], e["seq"]), reverse=True)
        return out[max(0, offset): max(0, offset) + max(0, limit)]

    def insert_rehydrate_event(self, row: dict) -> None:
        # NO FK check (unlike audit_events) — this table deliberately has no parent to require.
        self._rehydrate_events.append(dict(row))

    def list_rehydrate_events(self, resource_id: Optional[str], limit: int) -> list[dict]:
        rows = [dict(r) for r in self._rehydrate_events
                if resource_id is None or r["resource_id"] == resource_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)  # newest first
        return rows[: max(0, limit)]

    def upsert_deployment_attempt(self, row: dict) -> None:
        self._require_promotion(row["promotion_id"])
        existing = self._deployment_attempts.get(row["id"])
        if existing and existing["promotion_id"] != row["promotion_id"]:
            raise ValueError("deployment attempt belongs to another promotion")
        self._deployment_attempts[row["id"]] = dict(row)

    def list_deployment_attempts(self, promotion_id: str) -> list[dict]:
        rows = [dict(row) for row in self._deployment_attempts.values()
                if row["promotion_id"] == promotion_id]
        return sorted(rows, key=lambda row: (row["updated_at"], row["id"]))

    def healthcheck(self) -> None:
        return None

    def close(self) -> None:
        return None


# --- Postgres (Lakebase) backend --------------------------------------------

MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS promotions (
        -- ids are app-generated UUID strings (text, not the pg uuid type) so the Pg + in-memory
        -- backends round-trip identical str values (psycopg returns the uuid type as UUID objects).
        id                 text PRIMARY KEY,
        resource_id        text NOT NULL,
        resource_kind      text NOT NULL,
        resource_title     text,
        requester_email    text,
        pr_number          integer UNIQUE,
        pr_url             text,
        branch             text,
        current_phase      text,
        live_status        jsonb,
        terminal           boolean NOT NULL DEFAULT false,
        last_reconciled_at timestamptz,
        created_at         timestamptz NOT NULL,
        updated_at         timestamptz NOT NULL,
        access_spec        jsonb,
        audience_spec      jsonb,
        table_mapping      jsonb,
        change_provider    text,
        external_id        text,
        external_url       text,
        content_revision   text,
        engine_revision    text
    )""",
    # F2 (added after the original CREATE TABLE shipped): idempotent for a promotions table that
    # predates this column — the CREATE TABLE above already includes it for a fresh DB (no-op
    # there); ADD COLUMN IF NOT EXISTS covers a promotions table created before F2.
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS access_spec jsonb",
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS audience_spec jsonb",
    # G7: same idempotent-migration pattern as access_spec above, for the declared table de-para.
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS table_mapping jsonb",
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS change_provider text",
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS external_id text",
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS external_url text",
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS content_revision text",
    "ALTER TABLE promotions ADD COLUMN IF NOT EXISTS engine_revision text",
    """CREATE TABLE IF NOT EXISTS review_snapshots (
        id              text PRIMARY KEY,
        promotion_id    text NOT NULL REFERENCES promotions(id),
        created_at      timestamptz NOT NULL,
        gate_conclusion text,
        gate_summary    text,
        findings        jsonb,
        eval            jsonb,
        timeline        jsonb
    )""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        id                 text PRIMARY KEY,
        promotion_id       text NOT NULL REFERENCES promotions(id),
        seq                integer NOT NULL,
        occurred_at        timestamptz NOT NULL,
        event_type         text NOT NULL,
        actor_github_login text,
        actor_app_email    text,
        github_event_at    timestamptz,
        detail             jsonb,
        UNIQUE (promotion_id, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_promotions_requester ON promotions(requester_email)",
    "CREATE INDEX IF NOT EXISTS ix_promotions_resource  ON promotions(resource_id)",
    "CREATE INDEX IF NOT EXISTS ix_promotions_pr        ON promotions(pr_number)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_promotions_change_request "
    "ON promotions(change_provider, external_id) "
    "WHERE change_provider IS NOT NULL AND external_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_audit_promotion_seq  ON audit_events(promotion_id, seq)",
    # A reconcile-written milestone occurs AT MOST ONCE per promotion. This partial unique index makes
    # that a DB invariant, so two concurrent reconciles (e.g. a viewer poll + the LB6 scheduler) can't
    # double-record an event_type — the loser hits ON CONFLICT DO NOTHING. `requested`/`re_reviewed`
    # legitimately recur, so they're excluded.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_milestone ON audit_events(promotion_id, event_type) "
    "WHERE event_type NOT IN ('requested', 're_reviewed')",
    # F1 follow-up: a rehydrate with no linkable source Promotion — deliberately NO FK to
    # promotions(id) (see RehydrateEvent's docstring) and no seq (nothing to order WITHIN).
    """CREATE TABLE IF NOT EXISTS rehydrate_events (
        id             text PRIMARY KEY,
        resource_id    text NOT NULL,
        resource_title text,
        actor_email    text NOT NULL,
        mode           text NOT NULL,
        dev_space_id   text,
        detail         jsonb,
        created_at     timestamptz NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_rehydrate_events_resource ON rehydrate_events(resource_id)",
    """CREATE TABLE IF NOT EXISTS deployment_attempts (
        id                 text PRIMARY KEY,
        promotion_id       text NOT NULL REFERENCES promotions(id),
        provider           text NOT NULL,
        external_run_id    text NOT NULL,
        run_attempt        integer NOT NULL,
        revisions          jsonb NOT NULL,
        mutation_started   boolean NOT NULL DEFAULT false,
        completed_stages   jsonb NOT NULL,
        current_stage      text,
        failed_stage       text,
        target_ids         jsonb NOT NULL,
        reason             text,
        run_url            text,
        terminal_state     text NOT NULL,
        observed_at        timestamptz NOT NULL,
        updated_at         timestamptz NOT NULL,
        UNIQUE (promotion_id, provider, external_run_id, run_attempt)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_deployment_attempts_promotion "
    "ON deployment_attempts(promotion_id, updated_at)",
)

# Columns that hold JSON blobs (wrapped as jsonb on write).
_JSON_COLS = {"live_status", "findings", "eval", "timeline", "detail", "access_spec", "audience_spec", "table_mapping",
              "revisions", "completed_stages", "target_ids"}
_PROMO_COLS = ("id", "resource_id", "resource_kind", "resource_title", "requester_email",
               "pr_number", "pr_url", "branch", "current_phase", "live_status", "terminal",
               "last_reconciled_at", "created_at", "updated_at", "access_spec", "audience_spec",
               "table_mapping", "change_provider", "external_id", "external_url",
               "content_revision", "engine_revision")
_SNAP_COLS = ("id", "promotion_id", "created_at", "gate_conclusion", "gate_summary",
              "findings", "eval", "timeline")
_EVENT_COLS = ("id", "promotion_id", "seq", "occurred_at", "event_type", "actor_github_login",
               "actor_app_email", "github_event_at", "detail")
_REHYDRATE_COLS = ("id", "resource_id", "resource_title", "actor_email", "mode", "dev_space_id",
                   "detail", "created_at")
_ATTEMPT_COLS = ("id", "promotion_id", "provider", "external_run_id", "run_attempt", "revisions",
                 "mutation_started", "completed_stages", "current_stage", "failed_stage",
                 "target_ids", "reason", "run_url", "terminal_state", "observed_at", "updated_at")


import re as _re

_IDENT = _re.compile(r"^[a-z_][a-z0-9_]*$")  # safe SQL identifier (schema name is app config)


class PgBackend:
    """Lakebase-backed StoreBackend. Owns the SQL + a small OAuth-refreshing connection pool. psycopg
    is imported lazily so the offline suite (InMemoryBackend) needs no driver.

    Schema ownership (the Lakebase gotcha): the app SP has CAN_CONNECT_AND_CREATE — it can create NEW
    objects (incl. its own schema) but CANNOT create in the pre-existing `public` schema. So the
    store creates + uses a DEDICATED schema the SP owns (`SET search_path`), never `public`."""

    def __init__(self, conn_params: dict, token_provider: Callable[[], str], *,
                 schema: str = "genie_promote", min_size: int = 1, max_size: int = 5):
        import psycopg  # noqa: F401  (lazy — only the deployed app needs the driver)
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        if not _IDENT.match(schema):
            raise ValueError(f"unsafe Lakebase schema name {schema!r}")
        self._schema = schema
        self._dict_row = dict_row
        # Pin the SP-owned schema at CONNECT time via the libpq `options` param (a server GUC), not a
        # post-connect `SET` in a configure callback — the latter opens an uncommitted transaction on
        # every pooled connection and breaks pool init. This adds no extra round-trip.
        params = {**conn_params, "options": f"-c search_path={schema}"}

        class _OAuthConn(psycopg.Connection):
            # Each NEW connection mints a fresh short-lived token (the password) — no static secret.
            @classmethod
            def connect(cls, conninfo: str = "", **kw):
                kw["password"] = token_provider()
                return super().connect(conninfo, **kw)

        self._pool = ConnectionPool(
            kwargs={**params, "row_factory": dict_row}, connection_class=_OAuthConn,
            min_size=min_size, max_size=max_size, max_lifetime=2400,  # < the 1h token lifetime
            check=ConnectionPool.check_connection, open=False)
        self._pool.open(wait=True, timeout=30)

    def _jsonb(self, value):
        from psycopg.types.json import Jsonb
        return Jsonb(value) if value is not None else None

    def _insert(self, table: str, cols: tuple, row: dict) -> None:
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)

    def migrate(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Create the SP-owned schema first (the SP can CREATE here, not in `public`); the
            # connection's search_path already points at it, so the unqualified DDL lands inside it.
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            for stmt in MIGRATIONS:
                cur.execute(stmt)

    def insert_promotion(self, row: dict) -> None:
        from psycopg.errors import UniqueViolation
        try:
            self._insert("promotions", _PROMO_COLS, row)
        except UniqueViolation as e:  # UNIQUE(pr_number) -> map to the same type the fake raises
            raise DuplicatePRNumber(f"promotion already exists for PR #{row.get('pr_number')}") from e

    def get_promotion(self, promotion_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM promotions WHERE id = %s", (promotion_id,))

    def find_promotion_by_pr(self, pr_number: int) -> Optional[dict]:
        return self._one("SELECT * FROM promotions WHERE pr_number = %s", (pr_number,))

    def list_promotions(self, requester_email: Optional[str]) -> list[dict]:
        if requester_email is None:
            return self._all("SELECT * FROM promotions ORDER BY created_at DESC", ())
        return self._all(
            "SELECT * FROM promotions WHERE requester_email = %s ORDER BY created_at DESC",
            (requester_email,))

    def list_non_terminal(self) -> list[dict]:
        return self._all("SELECT * FROM promotions WHERE terminal = false ORDER BY created_at", ())

    def update_promotion(self, promotion_id: str, fields: dict) -> None:
        cols = list(fields)
        sets = ", ".join(f"{c} = %s" for c in cols)
        vals = [self._jsonb(fields[c]) if c in _JSON_COLS else fields[c] for c in cols]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE promotions SET {sets} WHERE id = %s", [*vals, promotion_id])

    def insert_snapshot(self, row: dict) -> None:
        self._insert("review_snapshots", _SNAP_COLS, row)

    def list_snapshots(self, promotion_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM review_snapshots WHERE promotion_id = %s ORDER BY created_at",
            (promotion_id,))

    def insert_audit_event(self, row: dict) -> Optional[int]:
        # Assign seq = next per-promotion sequence (COALESCE(MAX)+1). Two concurrent appends can both
        # pick N+1; UNIQUE(promotion_id, seq) rejects the loser -> RETRY (MAX advances, the next
        # attempt picks a free seq). SEPARATELY, the partial unique index (promotion_id, event_type)
        # makes a reconcile MILESTONE at-most-once: a concurrent double-reconcile hits ON CONFLICT
        # DO NOTHING -> no row -> RETURNING yields nothing -> return None (already recorded). The
        # conflict target matches the partial index (event_type NOT IN recurring), so requested/
        # re_reviewed are unaffected and always insert.
        from psycopg.errors import UniqueViolation
        cols = [c for c in _EVENT_COLS if c != "seq"]
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        recurring = ", ".join(f"'{e}'" for e in RECURRING_EVENTS)
        sql = (f"INSERT INTO audit_events (seq, {', '.join(cols)}) "
               f"SELECT COALESCE(MAX(seq), 0) + 1, {placeholders} "
               f"FROM audit_events WHERE promotion_id = %s "
               f"ON CONFLICT (promotion_id, event_type) WHERE event_type NOT IN ({recurring}) "
               f"DO NOTHING RETURNING seq")
        attempts = 5
        for attempt in range(attempts):
            try:
                with self._pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(sql, [*vals, row["promotion_id"]])
                    fetched = cur.fetchone()
                    return fetched["seq"] if fetched else None  # None = milestone already recorded
            except UniqueViolation:
                if attempt == attempts - 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def list_audit_events(self, promotion_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM audit_events WHERE promotion_id = %s ORDER BY seq", (promotion_id,))

    def list_recent_audit_events(
        self, limit: int, *, offset: int = 0,
        resource_id: Optional[str] = None, actor: Optional[str] = None,
        after: Optional[datetime] = None, before: Optional[datetime] = None,
    ) -> list[dict]:
        # ONE query (no per-Promotion N+1): join each event to its Promotion's resource fields,
        # newest-first, bounded. (promotion_id, seq) break ties deterministically. S4: optional
        # space/actor/date-range filters, all parameterized (never string-interpolated).
        where = []
        args: list = []
        if resource_id is not None:
            where.append("p.resource_id = %s")
            args.append(resource_id)
        if actor is not None:
            where.append("(ae.actor_github_login = %s OR ae.actor_app_email = %s)")
            args.extend([actor, actor])
        if after is not None:
            where.append("ae.occurred_at >= %s")
            args.append(after)
        if before is not None:
            where.append("ae.occurred_at <= %s")
            args.append(before)
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""
        args.extend([max(0, limit), max(0, offset)])
        return self._all(
            "SELECT ae.*, p.resource_id, p.resource_title "
            "FROM audit_events ae JOIN promotions p ON ae.promotion_id = p.id "
            f"{where_clause} "
            "ORDER BY ae.occurred_at DESC, ae.promotion_id DESC, ae.seq DESC "
            "LIMIT %s OFFSET %s", args)

    def insert_rehydrate_event(self, row: dict) -> None:
        self._insert("rehydrate_events", _REHYDRATE_COLS, row)

    def list_rehydrate_events(self, resource_id: Optional[str], limit: int) -> list[dict]:
        if resource_id is None:
            return self._all(
                "SELECT * FROM rehydrate_events ORDER BY created_at DESC LIMIT %s", (max(0, limit),))
        return self._all(
            "SELECT * FROM rehydrate_events WHERE resource_id = %s ORDER BY created_at DESC LIMIT %s",
            (resource_id, max(0, limit)))

    def upsert_deployment_attempt(self, row: dict) -> None:
        vals = [self._jsonb(row.get(col)) if col in _JSON_COLS else row.get(col)
                for col in _ATTEMPT_COLS]
        placeholders = ", ".join(["%s"] * len(_ATTEMPT_COLS))
        updates = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in _ATTEMPT_COLS
            if col not in {"id", "promotion_id", "provider", "external_run_id", "run_attempt"}
        )
        sql = (
            f"INSERT INTO deployment_attempts ({', '.join(_ATTEMPT_COLS)}) "
            f"VALUES ({placeholders}) ON CONFLICT (id) DO UPDATE SET {updates}"
        )
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)

    def list_deployment_attempts(self, promotion_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM deployment_attempts WHERE promotion_id = %s "
            "ORDER BY updated_at, id", (promotion_id,))

    def healthcheck(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def close(self) -> None:
        self._pool.close()

    def _one(self, sql: str, args: Iterable) -> Optional[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()

    def _all(self, sql: str, args: Iterable) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())


# --- startup wiring (hard dependency) ---------------------------------------


def _instance_for_host(w, host: str, override: Optional[str]) -> str:
    """Recover the Lakebase instance NAME from PGHOST (a generated endpoint DNS) by matching the
    listed instances' DNS — so the OAuth credential is scoped to the instance we connect to for ANY
    `lakebase_instance` value (NO hardcoded customer default; ADR-0004). An explicit
    `APP_LAKEBASE_INSTANCE` override wins; if neither discovery nor an override yields a name, raise
    (a loud, actionable error beats guessing a wrong instance). Mirrors scripts/verify_lakebase.py."""
    if override:
        return override
    try:
        for inst in w.database.list_database_instances():
            if host in (inst.read_write_dns, inst.read_only_dns):
                return inst.name
    except Exception:  # noqa: BLE001
        pass
    raise LakebaseUnavailable(
        f"could not resolve the Lakebase instance for PGHOST={host} (no DNS match and no "
        "APP_LAKEBASE_INSTANCE override) — set APP_LAKEBASE_INSTANCE to your instance name")


def _pg_backend_from_env(env: dict):
    """Build a PgBackend from the platform-injected PG* env, authenticating as the app SP via
    short-lived Lakebase OAuth (no static password)."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    host = env["PGHOST"]
    instance = _instance_for_host(w, host, env.get("APP_LAKEBASE_INSTANCE"))
    params = {
        "host": host, "port": int(env.get("PGPORT", "5432")),
        "dbname": env.get("PGDATABASE", "databricks_postgres"),
        "user": env["PGUSER"], "sslmode": env.get("PGSSLMODE", "require"),
    }

    def token_provider() -> str:
        return w.database.generate_database_credential(
            request_id="promotion-store", instance_names=[instance]).token

    # Dedicated SP-owned schema (config-driven, ADR-0004) — never `public` (the SP can't create there).
    schema = env.get("APP_LAKEBASE_SCHEMA", "genie_promote")
    return PgBackend(params, token_provider, schema=schema)


def build_store_from_env(*, env: Optional[dict] = None,
                         backend_factory: Optional[Callable[[], StoreBackend]] = None
                         ) -> Optional[PromotionStore]:
    """Build the store at startup. Lakebase is a HARD dependency of the DEPLOYED app:

    - `PGHOST` present (deployed, binding injected the PG* env) -> build the PgBackend, run
      idempotent migrations, and SELECT 1. ANY failure raises `LakebaseUnavailable` with a clear
      message — the app fails fast + loud, never silently half-broken.
    - `PGHOST` absent (local dev / tests with no Lakebase bound) -> return None so the app still runs
      (the persistence-backed endpoints, wired in LB3, surface a clear 'no store' error if hit) —
      UNLESS `APP_REQUIRE_STORE` is truthy, which makes a missing binding itself a fail-fast (the
      deployed app sets it, so a stripped/misconfigured bundle that drops the `database` binding is
      caught loudly at startup, not silently degraded).
    """
    env = os.environ if env is None else env
    if not env.get("PGHOST"):
        if str(env.get("APP_REQUIRE_STORE", "")).lower() in ("1", "true", "yes"):
            raise LakebaseUnavailable(
                "APP_REQUIRE_STORE is set but PGHOST is absent — the app's 'database' (Lakebase) "
                "resource binding is missing/misconfigured (ADR-0005 hard dependency)")
        return None
    factory = backend_factory or (lambda: _pg_backend_from_env(env))
    try:
        backend = factory()
        backend.migrate()       # idempotent — fresh DB self-initializes; re-run is a no-op
        backend.healthcheck()   # prove a real round-trip
    except LakebaseUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise LakebaseUnavailable(
            "Lakebase é dependência obrigatória e está indisponível na inicialização "
            f"(verifique a vinculação do recurso 'database' do app + a OAuth do SP): {e}") from e
    return PromotionStore(backend)

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
                         current_phase: Optional[str], live_status: Optional[dict]) -> Promotion:
        now = self._clock()
        p = Promotion(
            id=str(uuid.uuid4()), resource_id=resource_id, resource_kind=resource_kind,
            resource_title=resource_title, requester_email=requester_email, pr_number=pr_number,
            pr_url=pr_url, branch=branch, current_phase=current_phase, live_status=live_status,
            terminal=False, last_reconciled_at=None, created_at=now, updated_at=now)
        self._b.insert_promotion(dataclasses.asdict(p))
        return p

    def touch(self, promotion_id: str) -> None:
        """Bump `updated_at` (e.g. on a re-review that adds a snapshot but no cache change), so
        freshness/ordering reflect the activity even before the next reconcile."""
        self._b.update_promotion(promotion_id, {"updated_at": self._clock()})

    def get_promotion(self, promotion_id: str) -> Optional[Promotion]:
        row = self._b.get_promotion(promotion_id)
        return _as(Promotion, row)

    def find_by_pr(self, pr_number: int) -> Optional[Promotion]:
        return _as(Promotion, self._b.find_promotion_by_pr(pr_number))

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
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def insert_promotion(self, row: dict) -> None:
        pr = row.get("pr_number")
        if pr is not None and any(p.get("pr_number") == pr for p in self._promotions.values()):
            raise DuplicatePRNumber(f"promotion already exists for PR #{pr}")  # mirrors UNIQUE(pr_number)
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
        updated_at         timestamptz NOT NULL
    )""",
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
    "CREATE INDEX IF NOT EXISTS ix_audit_promotion_seq  ON audit_events(promotion_id, seq)",
    # A reconcile-written milestone occurs AT MOST ONCE per promotion. This partial unique index makes
    # that a DB invariant, so two concurrent reconciles (e.g. a viewer poll + the LB6 scheduler) can't
    # double-record an event_type — the loser hits ON CONFLICT DO NOTHING. `requested`/`re_reviewed`
    # legitimately recur, so they're excluded.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_milestone ON audit_events(promotion_id, event_type) "
    "WHERE event_type NOT IN ('requested', 're_reviewed')",
)

# Columns that hold JSON blobs (wrapped as jsonb on write).
_JSON_COLS = {"live_status", "findings", "eval", "timeline", "detail"}
_PROMO_COLS = ("id", "resource_id", "resource_kind", "resource_title", "requester_email",
               "pr_number", "pr_url", "branch", "current_phase", "live_status", "terminal",
               "last_reconciled_at", "created_at", "updated_at")
_SNAP_COLS = ("id", "promotion_id", "created_at", "gate_conclusion", "gate_summary",
              "findings", "eval", "timeline")
_EVENT_COLS = ("id", "promotion_id", "seq", "occurred_at", "event_type", "actor_github_login",
               "actor_app_email", "github_event_at", "detail")


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

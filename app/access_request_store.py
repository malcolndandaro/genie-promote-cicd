"""access_request_store — the durable index + append-only audit log for self-service access
requests (F3).

Mirrors `promotion_store.py`'s pattern EXACTLY (same reasons apply here): a `AccessRequestStore`
holds all domain logic (ids, timestamps, seq assignment, state machine) and delegates raw row
CRUD to an injectable **backend** (`InMemoryBackend` for offline tests, `PgBackend` for Lakebase).

Deliberately its OWN tables (`access_requests` / `access_request_audit_events`) rather than reusing
`promotion_store.audit_events` — that table's `promotion_id` column FKs to `promotions(id)`, and an
access request is not a Promotion (it may target a Space the requester has never promoted, and it
has its own state machine: `requested -> approved|denied -> applied`). Reusing it would either
require a fake/borrowed `promotion_id` (a lie in the schema) or a nullable FK that weakens the
existing 1:1-PR/append-only guarantees promotion_store's callers rely on. A sibling table keeps both
audit trails append-only + independently indexed, at the cost of a little duplication (accepted,
same tradeoff ADR-0005 already made for review_snapshots vs audit_events).

State machine (F3 acceptance criteria):
    requested -> approved -> applied   (approval succeeded; the governed grant was queued/applied)
    requested -> denied                (terminal)

`applied` is a DISTINCT phase from `approved` because approval and grant-application are two
separate acts (SoD: an approver decides; the governed pipeline — F2's sidecar->PR->apply_access.py
— is what actually lands the grant). A request can be `approved` but not yet `applied` if writing
the sidecar/PR failed after the decision was recorded (rare, but the two must not be conflated into
one atomic step or a partial failure would misreport an ungranted request as fully done).
"""
from __future__ import annotations

import dataclasses
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

# --- domain model -----------------------------------------------------------

STATES = ("requested", "approved", "denied", "applied")
TERMINAL_STATES = ("denied", "applied")

EVENT_TYPES = ("requested", "approved", "denied", "applied", "apply_failed")


@dataclasses.dataclass
class AccessRequest:
    id: str
    space_id: str
    space_title: Optional[str]
    requester_email: str                # verified identity at request time (never a header)
    note: Optional[str]
    want_space_permission: bool         # Genie Space CAN_RUN/CAN_VIEW (system 1, access_spec.py)
    space_permission_level: str         # "CAN_RUN" | "CAN_VIEW" — only meaningful if want_space_permission
    want_uc_select: bool                # UC SELECT on the space's data sources (system 2)
    state: str
    decided_by: Optional[str]           # the approver's verified identity (never the requester)
    decided_at: Optional[datetime]
    decision_note: Optional[str]
    pr_number: Optional[int]            # the sidecar-update PR the governed apply opened (if any)
    pr_url: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclasses.dataclass
class AccessRequestAuditEvent:
    id: str
    request_id: str
    seq: int
    occurred_at: datetime
    event_type: str
    actor_email: str                    # ALWAYS the verified acting identity — never a header
    detail: Optional[dict]


class SelfApprovalError(ValueError):
    """Raised when an approver's verified identity matches the request's requester (SoD, mirrors
    `prevent_self_review`). A `ValueError` subclass so it's easy to map to a 403 at the API layer
    without a bespoke except clause per caller."""


class InvalidTransition(ValueError):
    """Raised when a decision is attempted on a request that isn't in the `requested` state (e.g. a
    double-approve, or approving an already-denied request)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- backend interface --------------------------------------------------------


class AccessRequestBackend(Protocol):
    def migrate(self) -> None: ...
    def insert_request(self, row: dict) -> None: ...
    def get_request(self, request_id: str) -> Optional[dict]: ...
    def list_requests(self, requester_email: Optional[str], state: Optional[str]) -> list[dict]: ...
    def update_request(self, request_id: str, fields: dict) -> None: ...
    def insert_audit_event(self, row: dict) -> int: ...
    def list_audit_events(self, request_id: str) -> list[dict]: ...
    def healthcheck(self) -> None: ...
    def close(self) -> None: ...


# --- the store (domain logic; backend-agnostic) ------------------------------


class AccessRequestStore:
    def __init__(self, backend: AccessRequestBackend, *, clock: Callable[[], datetime] = _now):
        self._b = backend
        self._clock = clock

    def migrate(self) -> None:
        self._b.migrate()

    def close(self) -> None:
        self._b.close()

    # -- requests -------------------------------------------------------------
    def create_request(self, *, space_id: str, space_title: Optional[str], requester_email: str,
                       note: Optional[str] = None, want_space_permission: bool = True,
                       space_permission_level: str = "CAN_RUN", want_uc_select: bool = False
                       ) -> AccessRequest:
        """Persist a new access request in `requested` state + append the `requested` audit event
        (acting identity = the requester, since the requester is the one taking this action)."""
        if not requester_email:
            raise ValueError("requester_email is required (verified identity, never a header)")
        if not (want_space_permission or want_uc_select):
            raise ValueError("must request at least one of: Space permission, UC SELECT")
        now = self._clock()
        r = AccessRequest(
            id=str(uuid.uuid4()), space_id=space_id, space_title=space_title,
            requester_email=requester_email, note=note,
            want_space_permission=want_space_permission,
            space_permission_level=space_permission_level, want_uc_select=want_uc_select,
            state="requested", decided_by=None, decided_at=None, decision_note=None,
            pr_number=None, pr_url=None, created_at=now, updated_at=now)
        self._b.insert_request(dataclasses.asdict(r))
        self._append_event(r.id, "requested", actor_email=requester_email,
                           detail={"space_id": space_id, "note": note})
        return r

    def get_request(self, request_id: str) -> Optional[AccessRequest]:
        return _as(AccessRequest, self._b.get_request(request_id))

    def list_requests(self, *, requester_email: Optional[str] = None,
                      state: Optional[str] = None) -> list[AccessRequest]:
        """Requests, newest first. `requester_email=None` => all requesters (the caller enforces the
        approver role); `state=None` => any state."""
        return [_as(AccessRequest, r) for r in self._b.list_requests(requester_email, state)]

    # -- SoD-clean decision (approve/deny) ------------------------------------
    def decide(self, request_id: str, *, approve: bool, approver_email: str,
              decision_note: Optional[str] = None) -> AccessRequest:
        """Approve or deny a `requested` access request. SoD is enforced HERE, server-side, on the
        store's own data (never trusting a caller's claim about who the requester is): an approver
        whose verified identity matches the request's `requester_email` is REJECTED with
        `SelfApprovalError`, mirroring the promotion pipeline's `prevent_self_review`. A request not
        currently in `requested` state raises `InvalidTransition` (no re-deciding a terminal
        request)."""
        req = self.get_request(request_id)
        if req is None:
            raise ValueError(f"no access request {request_id!r}")
        if req.state != "requested":
            raise InvalidTransition(
                f"access request {request_id!r} is {req.state!r}, not 'requested' — cannot decide again")
        if approver_email.strip().lower() == req.requester_email.strip().lower():
            raise SelfApprovalError(
                "segregação de funções: o solicitante não pode aprovar/negar a própria solicitação")
        # S5 (app-ux-overhaul, D8): a denial without a reason defeats the whole point of adding
        # denial-reason clarity — a required field, not an optional courtesy. Approving still
        # needs no reason (only denial does). Server-enforced, not just a UI affordance.
        if not approve and not (decision_note and decision_note.strip()):
            raise ValueError("uma justificativa é obrigatória ao negar uma solicitação de acesso")
        now = self._clock()
        new_state = "approved" if approve else "denied"
        self._b.update_request(request_id, {
            "state": new_state, "decided_by": approver_email, "decided_at": now,
            "decision_note": decision_note, "updated_at": now})
        self._append_event(request_id, new_state, actor_email=approver_email,
                           detail={"note": decision_note} if decision_note else None)
        return self.get_request(request_id)

    def mark_applied(self, request_id: str, *, actor_email: str, pr_number: Optional[int] = None,
                     pr_url: Optional[str] = None, detail: Optional[dict] = None) -> AccessRequest:
        """Record that the GOVERNED grant path (sidecar -> bot PR -> apply_access.py on deploy) was
        successfully queued for an `approved` request. `actor_email` is the identity that triggered
        the apply (the approver, since applying happens as part of the approval flow) — recorded
        explicitly rather than assumed, so the audit event never silently drops the acting identity."""
        req = self.get_request(request_id)
        if req is None:
            raise ValueError(f"no access request {request_id!r}")
        if req.state != "approved":
            raise InvalidTransition(
                f"access request {request_id!r} is {req.state!r}, not 'approved' — cannot mark applied")
        now = self._clock()
        self._b.update_request(request_id, {
            "state": "applied", "pr_number": pr_number, "pr_url": pr_url, "updated_at": now})
        self._append_event(request_id, "applied", actor_email=actor_email,
                           detail={**(detail or {}), "pr_number": pr_number, "pr_url": pr_url})
        return self.get_request(request_id)

    def mark_apply_failed(self, request_id: str, *, actor_email: str, reason: str) -> AccessRequest:
        """Record that applying the governed grant FAILED after approval (e.g. the bot PR call
        raised). The request stays `approved` (not `applied`) — a distinct terminal-looking-but-not
        state an operator can retry/investigate, never silently reported as done."""
        req = self.get_request(request_id)
        if req is None:
            raise ValueError(f"no access request {request_id!r}")
        self._append_event(request_id, "apply_failed", actor_email=actor_email, detail={"reason": reason})
        return req

    # -- audit trail (append-only) ---------------------------------------------
    def _append_event(self, request_id: str, event_type: str, *, actor_email: str,
                      detail: Optional[dict] = None) -> AccessRequestAuditEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}")
        if not actor_email:
            raise ValueError("actor_email is required (verified identity, never a header)")
        row = {"id": str(uuid.uuid4()), "request_id": request_id, "occurred_at": self._clock(),
               "event_type": event_type, "actor_email": actor_email, "detail": detail}
        seq = self._b.insert_audit_event(row)
        return AccessRequestAuditEvent(seq=seq, **row)

    def list_audit_events(self, request_id: str) -> list[AccessRequestAuditEvent]:
        return [_as(AccessRequestAuditEvent, r) for r in self._b.list_audit_events(request_id)]

    def healthcheck(self) -> None:
        self._b.healthcheck()


def _as(cls, row: Optional[dict]):
    if row is None:
        return None
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: row[k] for k in names})


# --- in-memory backend (offline tests; no psycopg) --------------------------


class InMemoryBackend:
    """A faithful in-memory backend for offline tests — enforces append-only audit + per-request
    monotonic seq, mirroring `promotion_store.InMemoryBackend`."""

    def __init__(self):
        self._requests: dict[str, dict] = {}
        self._events: list[dict] = []
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def insert_request(self, row: dict) -> None:
        self._requests[row["id"]] = dict(row)

    def get_request(self, request_id: str) -> Optional[dict]:
        row = self._requests.get(request_id)
        return dict(row) if row else None

    def list_requests(self, requester_email: Optional[str], state: Optional[str]) -> list[dict]:
        rows = [dict(r) for r in self._requests.values()
                if (requester_email is None or r.get("requester_email") == requester_email)
                and (state is None or r.get("state") == state)]
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def update_request(self, request_id: str, fields: dict) -> None:
        if request_id in self._requests:
            self._requests[request_id].update(fields)

    def _require_request(self, request_id: str) -> None:
        if request_id not in self._requests:
            raise ValueError(f"no access request {request_id!r} (FK)")

    def insert_audit_event(self, row: dict) -> int:
        self._require_request(row["request_id"])
        seq = 1 + max((e["seq"] for e in self._events if e["request_id"] == row["request_id"]),
                      default=0)
        self._events.append({**row, "seq": seq})
        return seq

    def list_audit_events(self, request_id: str) -> list[dict]:
        rows = [dict(e) for e in self._events if e["request_id"] == request_id]
        return sorted(rows, key=lambda e: e["seq"])

    def healthcheck(self) -> None:
        return None

    def close(self) -> None:
        return None


# --- Postgres (Lakebase) backend --------------------------------------------

MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS access_requests (
        id                     text PRIMARY KEY,
        space_id               text NOT NULL,
        space_title            text,
        requester_email        text NOT NULL,
        note                   text,
        want_space_permission  boolean NOT NULL DEFAULT true,
        space_permission_level text NOT NULL DEFAULT 'CAN_RUN',
        want_uc_select         boolean NOT NULL DEFAULT false,
        state                  text NOT NULL DEFAULT 'requested',
        decided_by             text,
        decided_at             timestamptz,
        decision_note          text,
        pr_number              integer,
        pr_url                 text,
        created_at             timestamptz NOT NULL,
        updated_at             timestamptz NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS access_request_audit_events (
        id           text PRIMARY KEY,
        request_id   text NOT NULL REFERENCES access_requests(id),
        seq          integer NOT NULL,
        occurred_at  timestamptz NOT NULL,
        event_type   text NOT NULL,
        actor_email  text NOT NULL,
        detail       jsonb,
        UNIQUE (request_id, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_access_requests_requester ON access_requests(requester_email)",
    "CREATE INDEX IF NOT EXISTS ix_access_requests_state     ON access_requests(state)",
    "CREATE INDEX IF NOT EXISTS ix_access_requests_space     ON access_requests(space_id)",
    "CREATE INDEX IF NOT EXISTS ix_ar_audit_request_seq      ON access_request_audit_events(request_id, seq)",
)

_JSON_COLS = {"detail"}
_REQUEST_COLS = ("id", "space_id", "space_title", "requester_email", "note",
                 "want_space_permission", "space_permission_level", "want_uc_select", "state",
                 "decided_by", "decided_at", "decision_note", "pr_number", "pr_url",
                 "created_at", "updated_at")
_EVENT_COLS = ("id", "request_id", "seq", "occurred_at", "event_type", "actor_email", "detail")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgBackend:
    """Lakebase-backed AccessRequestBackend. Mirrors `promotion_store.PgBackend`'s OAuth-refreshing
    connection pool pattern + SP-owned-schema convention exactly (same reasons: the app SP can
    CREATE in its own schema but not in `public`). Kept as an independent pool/backend (not
    literally shared with `promotion_store.PromotionStore`'s internal pool) so this module stays
    self-sufficient and unit-testable in isolation, same as its sibling — the small cost is one
    extra (small, min_size=1) pool per deployed app, which is negligible next to a Postgres
    instance's connection budget."""

    def __init__(self, conn_params: dict, token_provider: Callable[[], str], *,
                 schema: str = "genie_promote", min_size: int = 1, max_size: int = 3):
        import psycopg  # noqa: F401  (lazy — only the deployed app needs the driver)
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        if not _IDENT.match(schema):
            raise ValueError(f"unsafe Lakebase schema name {schema!r}")
        self._schema = schema
        params = {**conn_params, "options": f"-c search_path={schema}"}

        class _OAuthConn(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo: str = "", **kw):
                kw["password"] = token_provider()
                return super().connect(conninfo, **kw)

        self._pool = ConnectionPool(
            kwargs={**params, "row_factory": dict_row}, connection_class=_OAuthConn,
            min_size=min_size, max_size=max_size, max_lifetime=2400, open=False,
            check=ConnectionPool.check_connection)
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
            # Idempotent: whichever store (this one or promotion_store's) starts up first creates
            # the shared SP-owned schema; the other's IF NOT EXISTS is then a no-op.
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            for stmt in MIGRATIONS:
                cur.execute(stmt)

    def insert_request(self, row: dict) -> None:
        self._insert("access_requests", _REQUEST_COLS, row)

    def get_request(self, request_id: str) -> Optional[dict]:
        return self._one("SELECT * FROM access_requests WHERE id = %s", (request_id,))

    def list_requests(self, requester_email: Optional[str], state: Optional[str]) -> list[dict]:
        clauses, args = [], []
        if requester_email is not None:
            clauses.append("requester_email = %s")
            args.append(requester_email)
        if state is not None:
            clauses.append("state = %s")
            args.append(state)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._all(f"SELECT * FROM access_requests {where} ORDER BY created_at DESC", args)

    def update_request(self, request_id: str, fields: dict) -> None:
        cols = list(fields)
        sets = ", ".join(f"{c} = %s" for c in cols)
        vals = [self._jsonb(fields[c]) if c in _JSON_COLS else fields[c] for c in cols]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE access_requests SET {sets} WHERE id = %s", [*vals, request_id])

    def insert_audit_event(self, row: dict) -> int:
        cols = [c for c in _EVENT_COLS if c != "seq"]
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO access_request_audit_events (seq, {', '.join(cols)}) "
               f"SELECT COALESCE(MAX(seq), 0) + 1, {placeholders} "
               f"FROM access_request_audit_events WHERE request_id = %s "
               f"RETURNING seq")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, [*vals, row["request_id"]])
            return cur.fetchone()["seq"]

    def list_audit_events(self, request_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM access_request_audit_events WHERE request_id = %s ORDER BY seq",
            (request_id,))

    def healthcheck(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def close(self) -> None:
        self._pool.close()

    def _one(self, sql: str, args) -> Optional[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()

    def _all(self, sql: str, args) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())


# --- startup wiring (mirrors promotion_store.build_store_from_env) ----------


def _pg_backend_from_env(env: dict):
    """Build a PgBackend from the platform-injected PG* env, authenticating as the app SP via
    short-lived Lakebase OAuth (no static password) — same connection recipe as
    `promotion_store._pg_backend_from_env`, reusing its instance-resolution helper so the two
    stores can never disagree about which Lakebase instance they're pointed at."""
    from databricks.sdk import WorkspaceClient

    import promotion_store  # local import: avoids a hard import-time cycle, mirrors sibling style

    w = WorkspaceClient()
    host = env["PGHOST"]
    instance = promotion_store._instance_for_host(w, host, env.get("APP_LAKEBASE_INSTANCE"))
    params = {
        "host": host, "port": int(env.get("PGPORT", "5432")),
        "dbname": env.get("PGDATABASE", "databricks_postgres"),
        "user": env["PGUSER"], "sslmode": env.get("PGSSLMODE", "require"),
    }

    def token_provider() -> str:
        return w.database.generate_database_credential(
            request_id="access-request-store", instance_names=[instance]).token

    schema = env.get("APP_LAKEBASE_SCHEMA", "genie_promote")
    return PgBackend(params, token_provider, schema=schema)


def build_store_from_env(*, env: Optional[dict] = None,
                         backend_factory: Optional[Callable[[], AccessRequestBackend]] = None
                         ) -> Optional[AccessRequestStore]:
    """Build the store at startup, mirroring `promotion_store.build_store_from_env`'s semantics
    EXACTLY (same hard-dependency contract): `PGHOST` present -> build + migrate + healthcheck,
    raising `LakebaseUnavailable`-shaped errors (imported from promotion_store, so callers catch ONE
    exception type across both stores) on any failure; `PGHOST` absent -> None (local/offline),
    UNLESS `APP_REQUIRE_STORE` is set, matching the same fail-fast-when-misconfigured contract."""
    from promotion_store import LakebaseUnavailable

    env = os.environ if env is None else env
    if not env.get("PGHOST"):
        if str(env.get("APP_REQUIRE_STORE", "")).lower() in ("1", "true", "yes"):
            raise LakebaseUnavailable(
                "APP_REQUIRE_STORE is set but PGHOST is absent — the app's 'database' (Lakebase) "
                "resource binding is missing/misconfigured (F3 access-request store)")
        return None
    factory = backend_factory or (lambda: _pg_backend_from_env(env))
    try:
        backend = factory()
        backend.migrate()
        backend.healthcheck()
    except LakebaseUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        raise LakebaseUnavailable(
            "Lakebase é dependência obrigatória e está indisponível na inicialização "
            f"(access-request store: verifique a vinculação do recurso 'database' + a OAuth do SP): {e}"
        ) from e
    return AccessRequestStore(backend)

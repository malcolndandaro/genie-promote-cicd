"""ka_endpoints_store — S7a (app-ux-overhaul): the durable, Lakebase-backed admin registry of
Knowledge Assistant (Agent Bricks) endpoints the reviewer can consult as an ADDITIVE advisory
source (D5) — never a replacement for the deterministic + custom rules.

Mirrors `rules_store.py`'s pattern EXACTLY (own tables, an injectable backend — `InMemoryBackend`
for offline tests, `PgBackend` for Lakebase, the same OAuth-refreshing connection-pool + SP-owned-
schema recipe, an append-only audit trail with its own soft rule_id-style reference). Unlike
`rules_store`'s upsert-by-rule_id, an endpoint has no natural admin-chosen key, so this store is
plain CRUD (create/update/delete by generated id), not upsert.

**Scope (GR1)**: an endpoint is EITHER scoped to one or more specific Genie Space ids, OR marked
`is_global` (applies to every space, always) — never both, never neither. `list_enabled_for_space`
is the query S7b's reviewer integration actually needs: every ENABLED endpoint whose scope
matches a given space (global, or that space id explicitly listed).

**Storage note**: filtering happens in Python over `list_all()`, not a SQL WHERE clause — this
table is small (admin-registered config, not an event stream), matching how `roles_store`/
`rules_store` already filter their own small config tables in Python rather than pushing every
predicate into SQL.
"""
from __future__ import annotations

import dataclasses
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

# --- domain model -------------------------------------------------------------

EVENT_TYPES = ("ka_endpoint_created", "ka_endpoint_updated", "ka_endpoint_deleted")


@dataclasses.dataclass
class KaEndpoint:
    id: str
    name: str                          # admin-facing label, e.g. "Handbook KA"
    serving_endpoint_name: str          # the actual Databricks serving-endpoint name (picked from
                                        # a live list — never typed, per GR1)
    is_global: bool                    # applies to every space, always
    scope_space_ids: list[str]         # specific space ids this endpoint applies to (empty if is_global)
    enabled: bool
    created_by: str                    # verified identity of the creator (display/audit convenience)
    created_at: datetime
    updated_at: datetime


@dataclasses.dataclass
class KaEndpointAuditEvent:
    id: str
    ka_endpoint_id: str
    seq: int
    occurred_at: datetime
    event_type: str
    actor_email: str                   # ALWAYS the verified acting identity — never a header
    detail: Optional[dict]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_scope(*, is_global: bool, scope_space_ids: Optional[list[str]]) -> list[str]:
    ids = list(scope_space_ids or [])
    if is_global and ids:
        raise ValueError("an endpoint cannot be both global and scoped to specific spaces")
    if not is_global and not ids:
        raise ValueError("a non-global endpoint needs at least one scoped space id")
    return ids


# --- backend interface ---------------------------------------------------------


class KaEndpointsBackend(Protocol):
    def migrate(self) -> None: ...
    def insert(self, row: dict) -> None: ...
    def update(self, id: str, fields: dict) -> None: ...
    def delete(self, id: str) -> None: ...
    def get(self, id: str) -> Optional[dict]: ...
    def list_all(self) -> list[dict]: ...
    def insert_audit_event(self, row: dict) -> int: ...
    def list_audit_events(self, ka_endpoint_id: str) -> list[dict]: ...
    def healthcheck(self) -> None: ...
    def close(self) -> None: ...


# --- the store (domain logic; backend-agnostic) --------------------------------


class KaEndpointsStore:
    def __init__(self, backend: KaEndpointsBackend, *, clock: Callable[[], datetime] = _now):
        self._b = backend
        self._clock = clock

    def migrate(self) -> None:
        self._b.migrate()

    def close(self) -> None:
        self._b.close()

    def healthcheck(self) -> None:
        self._b.healthcheck()

    # -- CRUD -----------------------------------------------------------------
    def create(self, *, name: str, serving_endpoint_name: str, actor_email: str,
              is_global: bool = False, scope_space_ids: Optional[list[str]] = None,
              enabled: bool = True) -> KaEndpoint:
        if not name or not name.strip():
            raise ValueError("name is required")
        if not serving_endpoint_name or not serving_endpoint_name.strip():
            raise ValueError("serving_endpoint_name is required")
        if not actor_email:
            raise ValueError("actor_email is required (verified identity, never a header)")
        ids = _validate_scope(is_global=is_global, scope_space_ids=scope_space_ids)
        now = self._clock()
        row = KaEndpoint(
            id=str(uuid.uuid4()), name=name.strip(), serving_endpoint_name=serving_endpoint_name.strip(),
            is_global=is_global, scope_space_ids=ids, enabled=enabled, created_by=actor_email,
            created_at=now, updated_at=now)
        self._b.insert(dataclasses.asdict(row))
        self._append_event(row.id, "ka_endpoint_created", actor_email=actor_email,
                           detail={"name": row.name, "serving_endpoint_name": row.serving_endpoint_name,
                                   "is_global": is_global, "scope_space_ids": ids})
        return row

    def update(self, id: str, *, actor_email: str, name: Optional[str] = None,
              serving_endpoint_name: Optional[str] = None, is_global: Optional[bool] = None,
              scope_space_ids: Optional[list[str]] = None, enabled: Optional[bool] = None) -> KaEndpoint:
        """Partial update — only fields explicitly passed change. Re-validates the scope invariant
        against the RESULTING state (existing values fill in whatever wasn't passed)."""
        existing = self._b.get(id)
        if existing is None:
            raise ValueError(f"no KA endpoint {id!r}")
        if not actor_email:
            raise ValueError("actor_email is required (verified identity, never a header)")
        next_is_global = existing["is_global"] if is_global is None else is_global
        next_scope = existing["scope_space_ids"] if scope_space_ids is None else scope_space_ids
        ids = _validate_scope(is_global=next_is_global, scope_space_ids=next_scope)
        fields = {
            "name": (name.strip() if name is not None else existing["name"]),
            "serving_endpoint_name": (serving_endpoint_name.strip() if serving_endpoint_name is not None
                                     else existing["serving_endpoint_name"]),
            "is_global": next_is_global, "scope_space_ids": ids,
            "enabled": (existing["enabled"] if enabled is None else enabled),
            "updated_at": self._clock(),
        }
        self._b.update(id, fields)
        self._append_event(id, "ka_endpoint_updated", actor_email=actor_email, detail=dict(fields, updated_at=None))
        return self.get(id)

    def delete(self, id: str, *, actor_email: str) -> None:
        """Idempotent — a no-op (no audit event) if already gone, mirroring the sibling stores."""
        existing = self._b.get(id)
        if existing is None:
            return
        self._b.delete(id)
        self._append_event(id, "ka_endpoint_deleted", actor_email=actor_email, detail=None)

    def get(self, id: str) -> Optional[KaEndpoint]:
        return _as(KaEndpoint, self._b.get(id))

    def list_all(self) -> list[KaEndpoint]:
        return [_as(KaEndpoint, r) for r in self._b.list_all()]

    def list_enabled_for_space(self, space_id: str) -> list[KaEndpoint]:
        """S7b's query: every ENABLED endpoint in scope for `space_id` (global, or explicitly
        listed) — the reviewer queries exactly these during a review run."""
        return [e for e in self.list_all()
               if e.enabled and (e.is_global or space_id in e.scope_space_ids)]

    # -- audit trail (append-only) ---------------------------------------------
    def _append_event(self, ka_endpoint_id: str, event_type: str, *, actor_email: str,
                      detail: Optional[dict] = None) -> KaEndpointAuditEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}")
        row = {"id": str(uuid.uuid4()), "ka_endpoint_id": ka_endpoint_id, "occurred_at": self._clock(),
               "event_type": event_type, "actor_email": actor_email, "detail": detail}
        seq = self._b.insert_audit_event(row)
        return KaEndpointAuditEvent(seq=seq, **row)

    def list_audit_events(self, ka_endpoint_id: str) -> list[KaEndpointAuditEvent]:
        return [_as(KaEndpointAuditEvent, r) for r in self._b.list_audit_events(ka_endpoint_id)]


def _as(cls, row: Optional[dict]):
    if row is None:
        return None
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: row[k] for k in names})


# --- in-memory backend (offline tests; no psycopg) -----------------------------


class InMemoryBackend:
    """A faithful in-memory backend for offline tests, mirroring the sibling stores' fakes."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._events: list[dict] = []
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def insert(self, row: dict) -> None:
        self._rows[row["id"]] = dict(row)

    def update(self, id: str, fields: dict) -> None:
        if id in self._rows:
            self._rows[id].update(fields)

    def delete(self, id: str) -> None:
        self._rows.pop(id, None)

    def get(self, id: str) -> Optional[dict]:
        row = self._rows.get(id)
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]

    def insert_audit_event(self, row: dict) -> int:
        seq = 1 + max((e["seq"] for e in self._events if e["ka_endpoint_id"] == row["ka_endpoint_id"]),
                      default=0)
        self._events.append({**row, "seq": seq})
        return seq

    def list_audit_events(self, ka_endpoint_id: str) -> list[dict]:
        rows = [dict(e) for e in self._events if e["ka_endpoint_id"] == ka_endpoint_id]
        return sorted(rows, key=lambda e: e["seq"])

    def healthcheck(self) -> None:
        return None

    def close(self) -> None:
        return None


# --- Postgres (Lakebase) backend -----------------------------------------------

MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS ka_endpoints (
        id                     text PRIMARY KEY,
        name                   text NOT NULL,
        serving_endpoint_name  text NOT NULL,
        is_global              boolean NOT NULL DEFAULT false,
        scope_space_ids        jsonb NOT NULL DEFAULT '[]',
        enabled                boolean NOT NULL DEFAULT true,
        created_by             text NOT NULL,
        created_at             timestamptz NOT NULL,
        updated_at             timestamptz NOT NULL
    )""",
    # Deliberately NO FK to ka_endpoints(id) — mirrors rule_audit_events: a delete must not be
    # blocked by (or cascade-destroy) its own audit history.
    """CREATE TABLE IF NOT EXISTS ka_endpoint_audit_events (
        id             text PRIMARY KEY,
        ka_endpoint_id text NOT NULL,
        seq            integer NOT NULL,
        occurred_at    timestamptz NOT NULL,
        event_type     text NOT NULL,
        actor_email    text NOT NULL,
        detail         jsonb,
        UNIQUE (ka_endpoint_id, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_ka_endpoint_audit_id_seq ON ka_endpoint_audit_events(ka_endpoint_id, seq)",
)

_JSON_COLS = {"scope_space_ids", "detail"}
_KA_COLS = ("id", "name", "serving_endpoint_name", "is_global", "scope_space_ids", "enabled",
           "created_by", "created_at", "updated_at")
_EVENT_COLS = ("id", "ka_endpoint_id", "seq", "occurred_at", "event_type", "actor_email", "detail")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgBackend:
    """Lakebase-backed KaEndpointsBackend. Mirrors `rules_store.PgBackend`'s OAuth-refreshing
    connection pool + SP-owned-schema convention exactly."""

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

    def migrate(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Idempotent: whichever store starts up first creates the shared SP-owned schema.
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            for stmt in MIGRATIONS:
                cur.execute(stmt)

    def insert(self, row: dict) -> None:
        cols = _KA_COLS
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = f"INSERT INTO ka_endpoints ({', '.join(cols)}) VALUES ({placeholders})"
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)

    def update(self, id: str, fields: dict) -> None:
        cols = list(fields.keys())
        sets = ", ".join(f"{c} = %s" for c in cols)
        vals = [self._jsonb(fields[c]) if c in _JSON_COLS else fields[c] for c in cols]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE ka_endpoints SET {sets} WHERE id = %s", [*vals, id])

    def delete(self, id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ka_endpoints WHERE id = %s", (id,))

    def get(self, id: str) -> Optional[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM ka_endpoints WHERE id = %s", (id,))
            return cur.fetchone()

    def list_all(self) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM ka_endpoints ORDER BY name")
            return list(cur.fetchall())

    def insert_audit_event(self, row: dict) -> int:
        cols = [c for c in _EVENT_COLS if c != "seq"]
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO ka_endpoint_audit_events (seq, {', '.join(cols)}) "
               f"SELECT COALESCE(MAX(seq), 0) + 1, {placeholders} "
               f"FROM ka_endpoint_audit_events WHERE ka_endpoint_id = %s "
               f"RETURNING seq")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, [*vals, row["ka_endpoint_id"]])
            return cur.fetchone()["seq"]

    def list_audit_events(self, ka_endpoint_id: str) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM ka_endpoint_audit_events WHERE ka_endpoint_id = %s ORDER BY seq",
                       (ka_endpoint_id,))
            return list(cur.fetchall())

    def healthcheck(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def close(self) -> None:
        self._pool.close()


# --- startup wiring (mirrors rules_store.build_store_from_env) ----------------


def _pg_backend_from_env(env: dict):
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
            request_id="ka-endpoints-store", instance_names=[instance]).token

    schema = env.get("APP_LAKEBASE_SCHEMA", "genie_promote")
    return PgBackend(params, token_provider, schema=schema)


def build_store_from_env(*, env: Optional[dict] = None,
                         backend_factory: Optional[Callable[[], KaEndpointsBackend]] = None
                         ) -> Optional[KaEndpointsStore]:
    """Build the KA endpoints store at startup, mirroring the sibling stores' hard-dependency
    contract EXACTLY: `PGHOST` present -> build + migrate + healthcheck, raising
    `LakebaseUnavailable` on any failure; `PGHOST` absent -> None (local/offline — no KA endpoints
    configured, the reviewer simply has none to query), UNLESS `APP_REQUIRE_STORE` is set."""
    from promotion_store import LakebaseUnavailable

    env = os.environ if env is None else env
    if not env.get("PGHOST"):
        if str(env.get("APP_REQUIRE_STORE", "")).lower() in ("1", "true", "yes"):
            raise LakebaseUnavailable(
                "APP_REQUIRE_STORE is set but PGHOST is absent — the app's 'database' (Lakebase) "
                "resource binding is missing/misconfigured (S7a ka_endpoints store)")
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
            f"(ka_endpoints store: verifique a vinculação do recurso 'database' + a OAuth do SP): {e}"
        ) from e
    return KaEndpointsStore(backend)

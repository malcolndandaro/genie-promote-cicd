"""rules_store — G2: the durable, Lakebase-backed admin configuration for the reviewer's handbook
rules (`genie_reviewer/handbook_rules.py` — 9 hardcoded ENV/GRANT/PII/EVAL/SQL rules).

Mirrors `roles_store.py`'s / `access_request_store.py`'s pattern EXACTLY (own tables, an injectable
**backend** — `InMemoryBackend` for offline tests, `PgBackend` for Lakebase, the same OAuth-
refreshing connection-pool + SP-owned-schema recipe): a `RulesStore` holds all domain logic
(upsert-by-rule_id, reset/delete, an append-only audit trail) and delegates raw row CRUD to the
backend.

An admin can, per hardcoded rule: **disable** it, **override its severity**, and **override its
params** (e.g. EVAL-01's `{"min_benchmarks": N}` threshold) — a row in `rule_overrides` with
`is_custom=False`. An admin can also **add a fully custom rule** (`is_custom=True`, its own
rule_id/severity/content/citation) that the reviewer prompt then grounds on alongside the 9
hardcoded ones.

**Its OWN audit table (`rule_audit_events`), NOT `promotion_store.audit_events`** — same reasoning
`access_request_store.py` already documents for the same choice: that table's `promotion_id`
column FKs to `promotions(id)`, and a rule change is not a Promotion. UNLIKE the sibling audit
tables, `rule_audit_events.rule_id` is deliberately **not** a foreign key into `rule_overrides`:
"reset to default" / "delete a custom rule" removes the `rule_overrides` row, and the audit trail
must survive that (an admin who resets EVAL-01's threshold back to default should still be able to
see the override that preceded it) — a hard FK would force choosing between an append-only audit
trail and a real reset/delete path. This module accepts that soft reference.

**The store-over-hardcoded fallback is the whole point (G2's acceptance criteria):** this store
only supplies override ROWS (plain dicts); the actual merge with `handbook_rules.RULES` is a PURE
function in `genie_reviewer/rules_config.py::effective_rules()`, which is byte-identical to the
hardcoded set when given no overrides (or no store at all) — offline CI and a fresh install behave
exactly as before this slice shipped. This module never imports `genie_reviewer` (kept as
import-clean/testable in isolation as every sibling store).
"""
from __future__ import annotations

import dataclasses
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

# --- domain model -------------------------------------------------------------

SEVERITIES = ("BLOCKER", "SUGGESTION", "STYLE")

EVENT_TYPES = ("rule_created", "rule_updated", "rule_deleted", "rule_reset")


@dataclasses.dataclass
class RuleOverride:
    rule_id: str
    is_custom: bool
    enabled: bool
    severity: Optional[str]   # override of the hardcoded severity_hint; the severity for a custom rule
    params: Optional[dict]    # e.g. {"min_benchmarks": 2} for EVAL-01 — opaque jsonb, rule-specific
    content: Optional[str]    # REQUIRED (non-empty) for a custom rule; unset for a plain override
    citation: Optional[str]   # REQUIRED (non-empty) for a custom rule; unset for a plain override
    updated_by: Optional[str]  # the verified identity of the last mutator (display/audit convenience)
    created_at: datetime
    updated_at: datetime


@dataclasses.dataclass
class RuleAuditEvent:
    id: str
    rule_id: str
    seq: int
    occurred_at: datetime
    event_type: str
    actor_email: str          # ALWAYS the verified acting identity — never a header
    detail: Optional[dict]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- backend interface ---------------------------------------------------------


class RulesBackend(Protocol):
    def migrate(self) -> None: ...
    def upsert(self, row: dict) -> None: ...
    def delete(self, rule_id: str) -> None: ...
    def get(self, rule_id: str) -> Optional[dict]: ...
    def list_all(self) -> list[dict]: ...
    def insert_audit_event(self, row: dict) -> int: ...
    def list_audit_events(self, rule_id: str) -> list[dict]: ...
    def healthcheck(self) -> None: ...
    def close(self) -> None: ...


# --- the store (domain logic; backend-agnostic) --------------------------------


class RulesStore:
    def __init__(self, backend: RulesBackend, *, clock: Callable[[], datetime] = _now):
        self._b = backend
        self._clock = clock

    def migrate(self) -> None:
        self._b.migrate()

    def close(self) -> None:
        self._b.close()

    def healthcheck(self) -> None:
        self._b.healthcheck()

    # -- CRUD -----------------------------------------------------------------
    def upsert(self, *, rule_id: str, actor_email: str, is_custom: bool = False,
              enabled: bool = True, severity: Optional[str] = None,
              params: Optional[dict] = None, content: Optional[str] = None,
              citation: Optional[str] = None) -> RuleOverride:
        """Create or update an override/custom rule, keyed on `rule_id` (idempotent upsert — mirrors
        `roles_store.assign`). A custom rule REQUIRES non-empty `severity`/`content`/`citation` (it
        has no hardcoded fallback to inherit them from); a plain override of a hardcoded rule needs
        none of those set (an override with nothing set is legal — e.g. `enabled=False` alone is a
        pure disable). Appends `rule_created` the first time a CUSTOM rule_id is written, else
        `rule_updated` (covers both a first-time override of a hardcoded rule and any later edit)."""
        if not rule_id or not rule_id.strip():
            raise ValueError("rule_id is required")
        rule_id = rule_id.strip()
        if severity is not None and severity not in SEVERITIES:
            raise ValueError(f"unknown severity {severity!r}; expected one of {SEVERITIES}")
        if is_custom:
            if not severity:
                raise ValueError("a custom rule requires severity")
            if not content or not content.strip():
                raise ValueError("a custom rule requires content")
            if not citation or not citation.strip():
                raise ValueError("a custom rule requires citation")
        if not actor_email:
            raise ValueError("actor_email is required (verified identity, never a header)")
        existing = self._b.get(rule_id)
        now = self._clock()
        row = RuleOverride(
            rule_id=rule_id, is_custom=is_custom, enabled=enabled, severity=severity,
            params=params, content=content, citation=citation, updated_by=actor_email,
            created_at=existing["created_at"] if existing else now, updated_at=now)
        self._b.upsert(dataclasses.asdict(row))
        event_type = "rule_created" if (existing is None and is_custom) else "rule_updated"
        self._append_event(rule_id, event_type, actor_email=actor_email,
                           detail={"enabled": enabled, "severity": severity, "params": params})
        return row

    def reset(self, rule_id: str, *, actor_email: str) -> None:
        """Remove an override, restoring the hardcoded default (for a plain override) or removing a
        custom rule entirely. A no-op — idempotent, no audit event — if nothing was there to reset
        (mirrors `roles_store.revoke`'s "already gone" semantics)."""
        existing = self._b.get(rule_id)
        if existing is None:
            return
        self._b.delete(rule_id)
        event_type = "rule_deleted" if existing.get("is_custom") else "rule_reset"
        self._append_event(rule_id, event_type, actor_email=actor_email, detail=None)

    def get(self, rule_id: str) -> Optional[RuleOverride]:
        return _as(RuleOverride, self._b.get(rule_id))

    def list_all(self) -> list[RuleOverride]:
        return [_as(RuleOverride, r) for r in self._b.list_all()]

    def list_all_dicts(self) -> list[dict]:
        """The raw override rows as plain dicts — what `rules_config.effective_rules()` consumes
        (kept dataclass-free at that boundary so the pure engine module has zero store dependency)."""
        return list(self._b.list_all())

    # -- audit trail (append-only) ---------------------------------------------
    def _append_event(self, rule_id: str, event_type: str, *, actor_email: str,
                      detail: Optional[dict] = None) -> RuleAuditEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}")
        row = {"id": str(uuid.uuid4()), "rule_id": rule_id, "occurred_at": self._clock(),
               "event_type": event_type, "actor_email": actor_email, "detail": detail}
        seq = self._b.insert_audit_event(row)
        return RuleAuditEvent(seq=seq, **row)

    def list_audit_events(self, rule_id: str) -> list[RuleAuditEvent]:
        return [_as(RuleAuditEvent, r) for r in self._b.list_audit_events(rule_id)]


def _as(cls, row: Optional[dict]):
    if row is None:
        return None
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: row[k] for k in names})


# --- in-memory backend (offline tests; no psycopg) -----------------------------


class InMemoryBackend:
    """A faithful in-memory backend for offline tests, mirroring the sibling stores' fakes — enforces
    append-only audit + per-rule monotonic seq, but NO FK check on insert_audit_event (unlike
    access_request_store's `_require_request`): a rule_audit_events row can legitimately outlive its
    rule_overrides row (see the module docstring)."""

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._events: list[dict] = []
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def upsert(self, row: dict) -> None:
        self._rows[row["rule_id"]] = dict(row)

    def delete(self, rule_id: str) -> None:
        self._rows.pop(rule_id, None)

    def get(self, rule_id: str) -> Optional[dict]:
        row = self._rows.get(rule_id)
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]

    def insert_audit_event(self, row: dict) -> int:
        seq = 1 + max((e["seq"] for e in self._events if e["rule_id"] == row["rule_id"]), default=0)
        self._events.append({**row, "seq": seq})
        return seq

    def list_audit_events(self, rule_id: str) -> list[dict]:
        rows = [dict(e) for e in self._events if e["rule_id"] == rule_id]
        return sorted(rows, key=lambda e: e["seq"])

    def healthcheck(self) -> None:
        return None

    def close(self) -> None:
        return None


# --- Postgres (Lakebase) backend -----------------------------------------------

MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS rule_overrides (
        rule_id     text PRIMARY KEY,
        is_custom   boolean NOT NULL DEFAULT false,
        enabled     boolean NOT NULL DEFAULT true,
        severity    text,
        params      jsonb,
        content     text,
        citation    text,
        updated_by  text,
        created_at  timestamptz NOT NULL,
        updated_at  timestamptz NOT NULL
    )""",
    # Deliberately NO FK to rule_overrides(rule_id) — see the module docstring: a reset/delete must
    # not be blocked by (or cascade-destroy) its own audit history.
    """CREATE TABLE IF NOT EXISTS rule_audit_events (
        id          text PRIMARY KEY,
        rule_id     text NOT NULL,
        seq         integer NOT NULL,
        occurred_at timestamptz NOT NULL,
        event_type  text NOT NULL,
        actor_email text NOT NULL,
        detail      jsonb,
        UNIQUE (rule_id, seq)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_rule_audit_rule_seq ON rule_audit_events(rule_id, seq)",
)

_JSON_COLS = {"params", "detail"}
_RULE_COLS = ("rule_id", "is_custom", "enabled", "severity", "params", "content", "citation",
              "updated_by", "created_at", "updated_at")
_EVENT_COLS = ("id", "rule_id", "seq", "occurred_at", "event_type", "actor_email", "detail")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgBackend:
    """Lakebase-backed RulesBackend. Mirrors `roles_store.PgBackend`'s OAuth-refreshing connection
    pool + SP-owned-schema convention exactly."""

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

    def upsert(self, row: dict) -> None:
        cols = _RULE_COLS
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("rule_id", "created_at"))
        sql = (f"INSERT INTO rule_overrides ({', '.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT (rule_id) DO UPDATE SET {sets}")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)

    def delete(self, rule_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM rule_overrides WHERE rule_id = %s", (rule_id,))

    def get(self, rule_id: str) -> Optional[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM rule_overrides WHERE rule_id = %s", (rule_id,))
            return cur.fetchone()

    def list_all(self) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM rule_overrides ORDER BY rule_id")
            return list(cur.fetchall())

    def insert_audit_event(self, row: dict) -> int:
        cols = [c for c in _EVENT_COLS if c != "seq"]
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO rule_audit_events (seq, {', '.join(cols)}) "
               f"SELECT COALESCE(MAX(seq), 0) + 1, {placeholders} "
               f"FROM rule_audit_events WHERE rule_id = %s "
               f"RETURNING seq")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, [*vals, row["rule_id"]])
            return cur.fetchone()["seq"]

    def list_audit_events(self, rule_id: str) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM rule_audit_events WHERE rule_id = %s ORDER BY seq", (rule_id,))
            return list(cur.fetchall())

    def healthcheck(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def close(self) -> None:
        self._pool.close()


# --- startup wiring (mirrors roles_store.build_store_from_env) ----------------


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
            request_id="rules-store", instance_names=[instance]).token

    schema = env.get("APP_LAKEBASE_SCHEMA", "genie_promote")
    return PgBackend(params, token_provider, schema=schema)


def build_store_from_env(*, env: Optional[dict] = None,
                         backend_factory: Optional[Callable[[], RulesBackend]] = None
                         ) -> Optional[RulesStore]:
    """Build the rules store at startup, mirroring the sibling stores' hard-dependency contract
    EXACTLY: `PGHOST` present -> build + migrate + healthcheck, raising `LakebaseUnavailable`
    (imported from `promotion_store`, so callers catch ONE exception type across all stores) on any
    failure; `PGHOST` absent -> None (local/offline — the engine falls back to the hardcoded rules),
    UNLESS `APP_REQUIRE_STORE` is set."""
    from promotion_store import LakebaseUnavailable

    env = os.environ if env is None else env
    if not env.get("PGHOST"):
        if str(env.get("APP_REQUIRE_STORE", "")).lower() in ("1", "true", "yes"):
            raise LakebaseUnavailable(
                "APP_REQUIRE_STORE is set but PGHOST is absent — the app's 'database' (Lakebase) "
                "resource binding is missing/misconfigured (G2 rules store)")
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
            f"(rules store: verifique a vinculação do recurso 'database' + a OAuth do SP): {e}"
        ) from e
    return RulesStore(backend)

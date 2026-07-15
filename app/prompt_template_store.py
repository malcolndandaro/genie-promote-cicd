"""prompt_template_store — S8 (app-ux-overhaul): the durable, Lakebase-backed admin override of
the reviewer's system-prompt PERSONA/POLICY text (`genie_reviewer/review_core.py`'s
`DEFAULT_PERSONA`) — never the PROTECTED_CORE (prompt-injection defense + JSON output schema),
which the app always appends regardless of what's stored here (enforced in `review_core.
build_review_prompt`, not this store — this store just holds text).

Mirrors the sibling stores' pattern (own table, injectable backend, OAuth-refreshing pool) but is
SIMPLER: there is exactly ONE current value (a singleton row), not a keyed collection — no
upsert-by-id, no per-item audit trail. GR2's resolution: current value + an append-only audit
log, NO version-history/rollback UI (an admin who wants a prior version has the audit log's text,
same as how `rule_overrides` handles this).

Validation (does a candidate template still produce parseable review output) is NOT this store's
job — it has no LLM access. That happens in `app_logic.validate_persona_template` BEFORE this
store is ever asked to save.
"""
from __future__ import annotations

import dataclasses
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

EVENT_TYPES = ("prompt_template_saved", "prompt_template_reset")


@dataclasses.dataclass
class PromptTemplate:
    template_text: str
    updated_by: str
    updated_at: datetime


@dataclasses.dataclass
class PromptTemplateAuditEvent:
    id: str
    seq: int
    occurred_at: datetime
    event_type: str
    actor_email: str          # ALWAYS the verified acting identity — never a header
    detail: Optional[dict]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- backend interface ---------------------------------------------------------


class PromptTemplateBackend(Protocol):
    def migrate(self) -> None: ...
    def get(self) -> Optional[dict]: ...
    def set(self, row: dict) -> None: ...
    def clear(self) -> None: ...
    def insert_audit_event(self, row: dict) -> int: ...
    def list_audit_events(self) -> list[dict]: ...
    def healthcheck(self) -> None: ...
    def close(self) -> None: ...


# --- the store (domain logic; backend-agnostic) --------------------------------


class PromptTemplateStore:
    def __init__(self, backend: PromptTemplateBackend, *, clock: Callable[[], datetime] = _now):
        self._b = backend
        self._clock = clock

    def migrate(self) -> None:
        self._b.migrate()

    def close(self) -> None:
        self._b.close()

    def healthcheck(self) -> None:
        self._b.healthcheck()

    def get(self) -> Optional[PromptTemplate]:
        """`None` = no custom template saved — the reviewer falls back to
        `review_core.DEFAULT_PERSONA` unchanged."""
        return _as(PromptTemplate, self._b.get())

    def save(self, *, template_text: str, actor_email: str) -> PromptTemplate:
        """Persist a NEW custom persona/policy template. The caller (engine_api) MUST have
        already validated this text produces parseable review output — this store trusts it.
        No version history: this REPLACES whatever was there. Audited with a before/after diff."""
        if not template_text or not template_text.strip():
            raise ValueError("template_text is required")
        if not actor_email:
            raise ValueError("actor_email is required (verified identity, never a header)")
        before = self._b.get()
        now = self._clock()
        row = PromptTemplate(template_text=template_text, updated_by=actor_email, updated_at=now)
        self._b.set(dataclasses.asdict(row))
        self._append_event("prompt_template_saved", actor_email=actor_email,
                           detail={"before": (before or {}).get("template_text"), "after": template_text})
        return row

    def reset(self, *, actor_email: str) -> None:
        """Revert to the hardcoded default (removes the custom row). Idempotent — a no-op (no
        audit event) if nothing was saved to begin with, mirroring the sibling stores'
        "already gone" semantics."""
        existing = self._b.get()
        if existing is None:
            return
        self._b.clear()
        self._append_event("prompt_template_reset", actor_email=actor_email,
                           detail={"before": existing.get("template_text")})

    # -- audit trail (append-only) ---------------------------------------------
    def _append_event(self, event_type: str, *, actor_email: str,
                      detail: Optional[dict] = None) -> PromptTemplateAuditEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {event_type!r}; expected one of {EVENT_TYPES}")
        row = {"id": str(uuid.uuid4()), "occurred_at": self._clock(),
               "event_type": event_type, "actor_email": actor_email, "detail": detail}
        seq = self._b.insert_audit_event(row)
        return PromptTemplateAuditEvent(seq=seq, **row)

    def list_audit_events(self) -> list[PromptTemplateAuditEvent]:
        return [_as(PromptTemplateAuditEvent, r) for r in self._b.list_audit_events()]


def _as(cls, row: Optional[dict]):
    if row is None:
        return None
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: row[k] for k in names})


# --- in-memory backend (offline tests; no psycopg) -----------------------------


class InMemoryBackend:
    """A faithful in-memory backend for offline tests, mirroring the sibling stores' fakes."""

    def __init__(self):
        self._row: Optional[dict] = None
        self._events: list[dict] = []
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def get(self) -> Optional[dict]:
        return dict(self._row) if self._row else None

    def set(self, row: dict) -> None:
        self._row = dict(row)

    def clear(self) -> None:
        self._row = None

    def insert_audit_event(self, row: dict) -> int:
        seq = 1 + len(self._events)
        self._events.append({**row, "seq": seq})
        return seq

    def list_audit_events(self) -> list[dict]:
        return sorted((dict(e) for e in self._events), key=lambda e: e["seq"])

    def healthcheck(self) -> None:
        return None

    def close(self) -> None:
        return None


# --- Postgres (Lakebase) backend -----------------------------------------------

MIGRATIONS = (
    # A true singleton table: exactly one row, always id='current'. Simpler than a real
    # single-row-enforcement constraint (e.g. a CHECK on a boolean) since every access already
    # goes through this store's get/set/clear, never raw SQL from elsewhere.
    """CREATE TABLE IF NOT EXISTS prompt_template (
        id            text PRIMARY KEY DEFAULT 'current',
        template_text text NOT NULL,
        updated_by    text NOT NULL,
        updated_at    timestamptz NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS prompt_template_audit_events (
        id          text PRIMARY KEY,
        seq         integer NOT NULL,
        occurred_at timestamptz NOT NULL,
        event_type  text NOT NULL,
        actor_email text NOT NULL,
        detail      jsonb,
        UNIQUE (seq)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_prompt_template_audit_seq ON prompt_template_audit_events(seq)",
)

_JSON_COLS = {"detail"}
_EVENT_COLS = ("id", "seq", "occurred_at", "event_type", "actor_email", "detail")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgBackend:
    """Lakebase-backed PromptTemplateBackend. Mirrors the sibling stores' OAuth-refreshing
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
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            for stmt in MIGRATIONS:
                cur.execute(stmt)

    def get(self) -> Optional[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT template_text, updated_by, updated_at FROM prompt_template WHERE id = 'current'")
            return cur.fetchone()

    def set(self, row: dict) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO prompt_template (id, template_text, updated_by, updated_at) "
                "VALUES ('current', %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET template_text = EXCLUDED.template_text, "
                "updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at",
                [row["template_text"], row["updated_by"], row["updated_at"]])

    def clear(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM prompt_template WHERE id = 'current'")

    def insert_audit_event(self, row: dict) -> int:
        cols = [c for c in _EVENT_COLS if c != "seq"]
        vals = [self._jsonb(row.get(c)) if c in _JSON_COLS else row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO prompt_template_audit_events (seq, {', '.join(cols)}) "
               f"SELECT COALESCE(MAX(seq), 0) + 1, {placeholders} FROM prompt_template_audit_events "
               f"RETURNING seq")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)
            return cur.fetchone()["seq"]

    def list_audit_events(self) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM prompt_template_audit_events ORDER BY seq")
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
            request_id="prompt-template-store", instance_names=[instance]).token

    schema = env.get("APP_LAKEBASE_SCHEMA", "genie_promote")
    return PgBackend(params, token_provider, schema=schema)


def build_store_from_env(*, env: Optional[dict] = None,
                         backend_factory: Optional[Callable[[], PromptTemplateBackend]] = None
                         ) -> Optional[PromptTemplateStore]:
    """Build the prompt-template store at startup, mirroring the sibling stores' hard-dependency
    contract EXACTLY: `PGHOST` present -> build + migrate + healthcheck, raising
    `LakebaseUnavailable` on any failure; `PGHOST` absent -> None (local/offline — the reviewer
    simply has no custom template, `DEFAULT_PERSONA` is used), UNLESS `APP_REQUIRE_STORE` is set."""
    from promotion_store import LakebaseUnavailable

    env = os.environ if env is None else env
    if not env.get("PGHOST"):
        if str(env.get("APP_REQUIRE_STORE", "")).lower() in ("1", "true", "yes"):
            raise LakebaseUnavailable(
                "APP_REQUIRE_STORE is set but PGHOST is absent — the app's 'database' (Lakebase) "
                "resource binding is missing/misconfigured (S8 prompt_template store)")
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
            f"(prompt_template store: verifique a vinculação do recurso 'database' + a OAuth do SP): {e}"
        ) from e
    return PromptTemplateStore(backend)

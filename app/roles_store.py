"""roles_store — F5 Phase 1: the durable, Lakebase-backed roles configuration (Steward/approver/
Admin) + the email<->GitHub-username mapping per approver.

Mirrors `access_request_store.py`'s pattern EXACTLY (own tables, `InMemoryBackend` for offline
tests, `PgBackend` for Lakebase, the same OAuth-refreshing connection-pool recipe): a `RolesStore`
holds all domain logic (ids, timestamps, upsert-by-email semantics) and delegates raw row CRUD to
an injectable backend.

Why a NEW store rather than folding roles into `promotion_store`/`access_request_store`: roles are
not an audit trail of an event stream — they're a small, mutable CONFIGURATION set (who is
Steward/Admin right now), read on nearly every request (`whoami`, every admin gate) and written
rarely (an admin changes an approver). A dedicated table keeps that hot read-path a single
`SELECT *` with no joins, and keeps the CRUD semantics (upsert-by-email, delete) distinct from the
append-only stores' contracts.

**Store-over-env precedence (the F5 acceptance criteria):** `effective_admin_emails()` /
`effective_steward_emails()` return the STORE's roles when the store holds ANY role rows at all;
only when the store is EMPTY (fresh install, or Lakebase absent) do the legacy `APP_ADMINS` /
`APP_STEWARDS` / `APP_STEWARD` env vars apply, as a **bootstrap fallback** — matching the PRD's "no
redeploy to change an approver" requirement (once a single role is configured in-app, env changes
no longer matter) while keeping today's env-only deployments working unchanged.

**Fail-closed (A2's guarantee, extended to F5):** an empty store AND empty/unset env vars must
resolve to an EMPTY admin set — never "everyone" or "no gate at all". Every helper here returns an
empty `frozenset`/list rather than raising or defaulting open.

**Phase 2 is explicitly NOT built here.** This store only holds the app's OWN desired-state config;
it never writes to GitHub. See `genie_reviewer/github_drift.py` for the read-only comparison.
"""
from __future__ import annotations

import dataclasses
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

# --- domain model -------------------------------------------------------------

# A role is one of these three. "steward" is the SoD approver (mirrors APP_STEWARD, singular by
# convention today but modeled as a role so multiple stewards are representable); "admin" mirrors
# APP_ADMINS (sees ALL promotions/console); "approver" is reserved for a future finer-grained role
# (F3's access-request approval today piggybacks on "admin") — included now so the schema doesn't
# need a migration when that distinction is actually needed.
ROLES = ("steward", "admin", "approver")

# The roles that grant admin-console/gate access (`_admin_emails` unions these). Removing the LAST
# of these while the store is otherwise non-empty locks EVERYONE out (env fallback only applies to a
# COMPLETELY empty store) — see `RolesStore.revoke`'s guard + `LastAdminError`.
_ADMIN_ROLES = ("admin", "steward")


class LastAdminError(ValueError):
    """Raised when a revoke would remove the last effective admin/steward while the store still holds
    other rows — which would drive the admin-gate set to empty (no env fallback for a non-empty
    store), 403-ing EVERYONE out of every admin endpoint including the role CRUD needed to recover.
    The operator must assign a replacement admin/steward first."""


@dataclasses.dataclass
class RoleAssignment:
    id: str
    email: str                       # the app-side identity (platform-verified email/user_name)
    role: str                        # one of ROLES
    github_username: Optional[str]   # email<->GitHub-username mapping (F5 US-33), or None if unset
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm_email(email: str) -> str:
    return email.strip().lower()


# --- backend interface ---------------------------------------------------------


class RolesBackend(Protocol):
    def migrate(self) -> None: ...
    def upsert(self, row: dict) -> None: ...
    def delete(self, email: str, role: str) -> None: ...
    def list_all(self) -> list[dict]: ...
    def healthcheck(self) -> None: ...
    def close(self) -> None: ...


# --- the store (domain logic; backend-agnostic) --------------------------------


class RolesStore:
    def __init__(self, backend: RolesBackend, *, clock: Callable[[], datetime] = _now):
        self._b = backend
        self._clock = clock

    def migrate(self) -> None:
        self._b.migrate()

    def close(self) -> None:
        self._b.close()

    def healthcheck(self) -> None:
        self._b.healthcheck()

    # -- CRUD -----------------------------------------------------------------
    def assign(self, *, email: str, role: str, github_username: Optional[str] = None) -> RoleAssignment:
        """Assign (or update) a role for an email — idempotent upsert keyed on (email, role), so
        re-assigning the same role just refreshes `github_username`/`updated_at` rather than
        creating a duplicate row. Raises `ValueError` on an unknown role (a 400 at the API layer,
        never a silently-ignored typo'd role that then makes `_is_admin` behave unexpectedly)."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
        if not email or not email.strip():
            raise ValueError("email is required")
        email = _norm_email(email)
        existing = self._find(email, role)
        now = self._clock()
        row = RoleAssignment(
            id=existing["id"] if existing else str(uuid.uuid4()),
            email=email, role=role,
            github_username=(github_username.strip() if github_username else None),
            created_at=existing["created_at"] if existing else now,
            updated_at=now,
        )
        self._b.upsert(dataclasses.asdict(row))
        return row

    def revoke(self, *, email: str, role: str) -> None:
        """Remove a role assignment. A no-op if it didn't exist (idempotent, mirrors DELETE
        semantics elsewhere in the app — never a 404 for "already gone").

        LOCKOUT GUARD (F5 review): if this row is an existing admin/steward and removing it would
        leave the store non-empty but with ZERO admin/steward rows, raise `LastAdminError` — every
        admin endpoint (incl. this CRUD) would otherwise 403 for everyone, with no env fallback
        (env only backs a COMPLETELY empty store). Revoking down to a fully-empty store IS allowed
        (env fallback then applies), as is revoking a non-admin role."""
        ne = _norm_email(email)
        if role in _ADMIN_ROLES:
            rows = self.list_all()
            removing = any(_norm_email(r.email) == ne and r.role == role for r in rows)
            if removing:
                remaining = [r for r in rows if not (_norm_email(r.email) == ne and r.role == role)]
                if remaining and not any(r.role in _ADMIN_ROLES for r in remaining):
                    raise LastAdminError(
                        "cannot remove the last admin/steward — assign a replacement first")
        self._b.delete(ne, role)

    def list_all(self) -> list[RoleAssignment]:
        return [_as(RoleAssignment, r) for r in self._b.list_all()]

    def _find(self, email: str, role: str) -> Optional[dict]:
        for r in self._b.list_all():
            if r["email"] == email and r["role"] == role:
                return r
        return None

    # -- effective role sets (store-over-env precedence) -----------------------
    def effective_emails(self, role: str, *, env_fallback: frozenset[str]) -> frozenset[str]:
        """The effective set of emails holding `role`, applying the F5 precedence: if the store
        holds ANY role rows AT ALL (any role, not just this one — a store that's been configured
        for *anything* is no longer "empty"), the store is authoritative for every role and
        `env_fallback` is ignored EVEN IF this specific role has zero store rows (an admin who
        configures a Steward in-app but leaves Admins unconfigured should see an EMPTY admin set,
        not silently fall back to the env var — that's the "no false sense of control" the PRD
        calls out, applied to precedence itself). Only a COMPLETELY empty store (fresh install,
        nothing configured yet) falls back to env. Fails closed either way: an empty result here
        propagates as "no one has this role", never "everyone"."""
        all_rows = self._b.list_all()
        if not all_rows:
            return env_fallback
        return frozenset(r["email"] for r in all_rows if r["role"] == role)

    def github_username_for(self, email: str) -> Optional[str]:
        """The mapped GitHub username for an app-side email, or None if unmapped (any role)."""
        email = _norm_email(email)
        for r in self._b.list_all():
            if r["email"] == email and r.get("github_username"):
                return r["github_username"]
        return None


def _as(cls, row: dict):
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: row[k] for k in names})


# --- in-memory backend (offline tests; no psycopg) -----------------------------


class InMemoryBackend:
    """A faithful in-memory backend for offline tests, mirroring the sibling stores' fakes."""

    def __init__(self):
        self._rows: dict[tuple[str, str], dict] = {}  # (email, role) -> row
        self.migrated = False

    def migrate(self) -> None:
        self.migrated = True

    def upsert(self, row: dict) -> None:
        self._rows[(row["email"], row["role"])] = dict(row)

    def delete(self, email: str, role: str) -> None:
        self._rows.pop((email, role), None)

    def list_all(self) -> list[dict]:
        return [dict(r) for r in self._rows.values()]

    def healthcheck(self) -> None:
        return None

    def close(self) -> None:
        return None


# --- Postgres (Lakebase) backend -----------------------------------------------

MIGRATIONS = (
    """CREATE TABLE IF NOT EXISTS roles (
        id               text PRIMARY KEY,
        email            text NOT NULL,
        role             text NOT NULL,
        github_username  text,
        created_at       timestamptz NOT NULL,
        updated_at       timestamptz NOT NULL,
        UNIQUE (email, role)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_roles_role ON roles(role)",
)

_ROLE_COLS = ("id", "email", "role", "github_username", "created_at", "updated_at")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PgBackend:
    """Lakebase-backed RolesBackend. Mirrors `access_request_store.PgBackend`'s OAuth-refreshing
    connection-pool + SP-owned-schema convention exactly."""

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

    def migrate(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Idempotent: whichever store starts up first creates the shared SP-owned schema.
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            for stmt in MIGRATIONS:
                cur.execute(stmt)

    def upsert(self, row: dict) -> None:
        cols = _ROLE_COLS
        vals = [row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO roles ({', '.join(cols)}) VALUES ({placeholders}) "
               f"ON CONFLICT (email, role) DO UPDATE SET "
               f"github_username = EXCLUDED.github_username, updated_at = EXCLUDED.updated_at")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)

    def delete(self, email: str, role: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM roles WHERE email = %s AND role = %s", (email, role))

    def list_all(self) -> list[dict]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM roles ORDER BY role, email")
            return list(cur.fetchall())

    def healthcheck(self) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    def close(self) -> None:
        self._pool.close()


# --- startup wiring (mirrors access_request_store.build_store_from_env) --------


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
            request_id="roles-store", instance_names=[instance]).token

    schema = env.get("APP_LAKEBASE_SCHEMA", "genie_promote")
    return PgBackend(params, token_provider, schema=schema)


def build_store_from_env(*, env: Optional[dict] = None,
                         backend_factory: Optional[Callable[[], RolesBackend]] = None
                         ) -> Optional[RolesStore]:
    """Build the roles store at startup, mirroring the sibling stores' hard-dependency contract
    EXACTLY: `PGHOST` present -> build + migrate + healthcheck, raising `LakebaseUnavailable`
    (imported from `promotion_store`, so callers catch ONE exception type across all stores) on any
    failure; `PGHOST` absent -> None (local/offline), UNLESS `APP_REQUIRE_STORE` is set."""
    from promotion_store import LakebaseUnavailable

    env = os.environ if env is None else env
    if not env.get("PGHOST"):
        if str(env.get("APP_REQUIRE_STORE", "")).lower() in ("1", "true", "yes"):
            raise LakebaseUnavailable(
                "APP_REQUIRE_STORE is set but PGHOST is absent — the app's 'database' (Lakebase) "
                "resource binding is missing/misconfigured (F5 roles store)")
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
            f"(roles store: verifique a vinculação do recurso 'database' + a OAuth do SP): {e}"
        ) from e
    return RolesStore(backend)

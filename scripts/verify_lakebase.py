#!/usr/bin/env python3
"""verify_lakebase.py — connectivity smoke for the Lakebase instance (LB1 acceptance).

Connects to the per-deployment Lakebase Postgres and runs `SELECT 1`, authenticating with a
**short-lived OAuth credential** minted via the Databricks SDK — there is NO static password
anywhere (US-11). Two modes, auto-detected:

  - **Deployed app (SP):** the platform injects PG* env (PGHOST/PGPORT/PGDATABASE/PGUSER/PGSSLMODE)
    when the app has a `database` resource bound (databricks.yml). The app SP is the Postgres user.
  - **Local (operator):** pass `--profile <profile>` (+ optional `--instance <name>`); resolves the
    instance host via the SDK and connects as the caller's own identity (project creators have
    `databricks_superuser`). This proves the instance is reachable + OAuth works end-to-end.

Usage:
  python scripts/verify_lakebase.py --profile cerc-mlops-dev            # local
  python scripts/verify_lakebase.py --profile cerc-mlops-dev --instance genie-promote
  python scripts/verify_lakebase.py                                     # on the deployed app (env)
"""
from __future__ import annotations

import argparse
import os
import sys

from databricks.sdk import WorkspaceClient


def _instance_for_host(w: WorkspaceClient, host: str, fallback: str) -> str:
    """The Lakebase instance NAME whose endpoint matches `host`.

    In deployed mode the platform injects PGHOST (a generated endpoint DNS, NOT the instance name),
    so we can't assume the instance is named after any default. Match the host against the listed
    instances' read/write (or read-only) DNS to recover the real name — fully config-independent, so
    `generate_database_credential` is always scoped to the instance we actually connect to (works for
    ANY `lakebase_instance` value; no hardcoded default leaks in). Falls back to `fallback` if no
    match (e.g. list permission missing)."""
    try:
        for inst in w.database.list_database_instances():
            if host in (inst.read_write_dns, inst.read_only_dns):
                return inst.name
    except Exception:  # noqa: BLE001 — fall back rather than fail the smoke on a list hiccup
        pass
    return fallback


def _conn_params(profile: str | None, instance: str) -> dict:
    """Resolve host/user/dbname + a fresh OAuth token for the Lakebase instance.

    Prefers the platform-injected PG* env (deployed app, SP identity); otherwise resolves the
    instance by name via the SDK and connects as the caller (local operator). Always OAuth — the
    password is a 1-hour token, never persisted.
    """
    w = WorkspaceClient(profile=profile) if profile else WorkspaceClient()

    pghost = os.environ.get("PGHOST")
    if pghost:  # deployed app: env is authoritative, user is the SP client id (PGUSER)
        host = pghost
        user = os.environ["PGUSER"]
        dbname = os.environ.get("PGDATABASE", "databricks_postgres")
        port = int(os.environ.get("PGPORT", "5432"))
        # Recover the instance name from PGHOST — never trust the CLI default in deployed mode.
        instance = _instance_for_host(w, host, instance)
    else:  # local operator: resolve the instance, connect as the caller's identity
        inst = w.database.get_database_instance(name=instance)
        if inst.state and str(inst.state.value if hasattr(inst.state, "value") else inst.state) != "AVAILABLE":
            print(f"WARNING: instance {instance} state={inst.state} (not AVAILABLE) — connect may fail")
        host = inst.read_write_dns
        user = w.current_user.me().user_name
        dbname = "databricks_postgres"
        port = 5432

    token = w.database.generate_database_credential(
        request_id="verify-lakebase", instance_names=[instance]).token
    return {"host": host, "port": port, "dbname": dbname, "user": user,
            "password": token, "sslmode": "require"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Lakebase connectivity smoke (SELECT 1 via OAuth)")
    ap.add_argument("--profile", default=None, help="CLI profile (local mode); omit on the deployed app")
    ap.add_argument("--instance", default=os.environ.get("APP_LAKEBASE_INSTANCE", "genie-promote"),
                    help="Lakebase instance name (default: genie-promote / $APP_LAKEBASE_INSTANCE)")
    args = ap.parse_args()

    import psycopg  # local import so the script errors clearly if the driver isn't installed

    params = _conn_params(args.profile, args.instance)
    safe = {k: v for k, v in params.items() if k != "password"}
    print(f"connecting: {safe}")
    with psycopg.connect(**params, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            (one,) = cur.fetchone()
            cur.execute("SELECT current_user, current_database(), version()")
            who, db, ver = cur.fetchone()
    assert one == 1, f"SELECT 1 returned {one!r}"
    print(f"OK — SELECT 1 == 1  | current_user={who} db={db}")
    print(f"     {ver.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

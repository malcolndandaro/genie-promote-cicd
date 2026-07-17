#!/usr/bin/env python3
"""Reset only disposable demo execution history in Lakebase.

The pilot treats promotion history as demo data. This command deliberately preserves operational
configuration (roles, rules and their audit, reviewer prompt, and Knowledge Assistant registry).
It is dry-run by default and requires both ``--execute`` and an exact confirmation phrase.
"""
from __future__ import annotations

import argparse
import os
import sys

from databricks.sdk import WorkspaceClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from promotion_store import _IDENT, PgBackend  # noqa: E402


LEDGER_TABLES = (
    "deployment_attempts",
    "audit_events",
    "review_snapshots",
    "rehydrate_events",
    "promotions",
)
PRESERVED_CONFIG_TABLES = (
    "roles",
    "rule_overrides",
    "rule_audit_events",
    "prompt_template",
    "prompt_template_audit_events",
    "ka_endpoints",
    "ka_endpoint_audit_events",
)
CONFIRMATION = "DELETE-DEMO-LEDGER"


def snapshot_counts(cursor) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in LEDGER_TABLES:
        cursor.execute(f"SELECT COUNT(*) AS n FROM {table}")
        row = cursor.fetchone()
        counts[table] = int(row["n"] if isinstance(row, dict) else row[0])
    return counts


def reset_with_cursor(cursor) -> None:
    """Delete children before parents; safe and idempotent on the canonical schema."""
    for table in LEDGER_TABLES:
        cursor.execute(f"DELETE FROM {table}")


def _backend(profile: str, instance: str, schema: str) -> PgBackend:
    w = WorkspaceClient(profile=profile)
    inst = w.database.get_database_instance(name=instance)
    params = {
        "host": inst.read_write_dns,
        "port": 5432,
        "dbname": "databricks_postgres",
        "user": w.current_user.me().user_name,
        "sslmode": "require",
    }

    def token_provider() -> str:
        return w.database.generate_database_credential(
            request_id="reset-demo-ledger", instance_names=[instance]
        ).token

    return PgBackend(params, token_provider, schema=schema)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--instance", default="genie-promote")
    parser.add_argument("--schema", default="genie_promote")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not _IDENT.match(args.schema):
        parser.error(f"unsafe schema name {args.schema!r}")

    backend = _backend(args.profile, args.instance, args.schema)
    try:
        backend.migrate()
        with backend._pool.connection() as conn, conn.cursor() as cursor:
            before = snapshot_counts(cursor)
            print("Disposable ledger rows:")
            for table, count in before.items():
                print(f"  {table}: {count}")
            print("Preserved configuration tables: " + ", ".join(PRESERVED_CONFIG_TABLES))
            if not args.execute:
                print(f"Dry run only. Re-run with --execute --confirm {CONFIRMATION}")
                return 0
            if args.confirm != CONFIRMATION:
                parser.error(f"--confirm must be exactly {CONFIRMATION}")
            reset_with_cursor(cursor)
            after = snapshot_counts(cursor)
            if any(after.values()):
                raise RuntimeError(f"ledger reset verification failed: {after}")
            print("Demo ledger reset complete; operational configuration was preserved.")
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())

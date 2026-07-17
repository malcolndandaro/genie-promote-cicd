#!/usr/bin/env python3
"""verify_lakebase_store.py — prove the PgBackend SQL + migrations against REAL Lakebase (LB2).

The offline suite (tests/test_promotion_store.py) tests the store CONTRACT via InMemoryBackend with
no live DB ('no live Lakebase in CI'). This script proves the actual Postgres path: it runs the
idempotent migrations, then a full round-trip (create / get / list / Change Request lookup / snapshot append /
append-only audit with monotonic seq / status-cache update) against the dev Lakebase instance,
authenticating via short-lived OAuth (no static password), and CLEANS UP its test rows after.

  python scripts/verify_lakebase_store.py --profile cerc-mlops-dev [--instance genie-promote]

Run locally (the runner host is IP-allowlisted); NOT wired into CI.
"""
from __future__ import annotations

import argparse
import os
import sys

from databricks.sdk import WorkspaceClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from promotion_store import _IDENT, DuplicateChangeRequest, PgBackend, PromotionStore  # noqa: E402

_TEST_EXTERNAL_PREFIX = "verify-"


def _backend(profile: str, instance: str, schema: str) -> PgBackend:
    w = WorkspaceClient(profile=profile)
    inst = w.database.get_database_instance(name=instance)
    params = {"host": inst.read_write_dns, "port": 5432, "dbname": "databricks_postgres",
              "user": w.current_user.me().user_name, "sslmode": "require"}

    def token_provider() -> str:
        return w.database.generate_database_credential(
            request_id="verify-store", instance_names=[instance]).token

    return PgBackend(params, token_provider, schema=schema)


def _purge_test_rows(backend: PgBackend) -> None:
    """Delete any leftover verifier promotions + their children. Test-only raw SQL
    — the store itself stays append-only (no delete API)."""
    with backend._pool.connection() as conn, conn.cursor() as cur:
        pattern = _TEST_EXTERNAL_PREFIX + "%"
        for table in ("deployment_attempts", "audit_events", "review_snapshots"):
            cur.execute(
                f"DELETE FROM {table} WHERE promotion_id IN "
                "(SELECT id FROM promotions WHERE change_provider = 'verifier' AND external_id LIKE %s)",
                (pattern,),
            )
        cur.execute(
            "DELETE FROM promotions WHERE change_provider = 'verifier' AND external_id LIKE %s",
            (pattern,),
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--instance", default="genie-promote")
    # Use a THROWAWAY schema by default so a local run never grabs ownership of the app SP's
    # `genie_promote` schema (the SP must own that one). The throwaway is dropped at the end.
    ap.add_argument("--schema", default="lb_verify_local")
    args = ap.parse_args()
    if not _IDENT.match(args.schema):
        ap.error(f"unsafe schema name {args.schema!r}")

    backend = _backend(args.profile, args.instance, args.schema)
    store = PromotionStore(backend)

    print(f"migrate() into schema {args.schema!r} (idempotent — running twice)")
    store.migrate(); store.migrate()

    _purge_test_rows(backend)  # clean any leftovers from a prior failed run
    external_id = _TEST_EXTERNAL_PREFIX + "1"
    try:
        p = store.create_promotion(
            resource_id="space-verify", resource_kind="genie_space", resource_title="Verify",
            requester_email="verify@acme.com", branch="promote/verify", current_phase="open",
            live_status={"phase": "open"}, change_provider="verifier", external_id=external_id,
            external_url="https://example.invalid/change/1",
            audience_spec={"principals": [{"principal": "users", "is_group": True}]})
        assert store.get_promotion(p.id).external_id == external_id, "get round-trip"
        assert store.find_by_change_request("verifier", external_id).id == p.id, "lookup"
        assert any(x.id == p.id for x in store.list_promotions("verify@acme.com")), "list mine"

        # A second promotion for the same Change Request raises the provider-neutral duplicate.
        try:
            store.create_promotion(resource_id="dup", resource_kind="genie_space",
                                   resource_title="dup", requester_email="x@acme.com", branch="b",
                                   current_phase="open", live_status={}, change_provider="verifier",
                                   external_id=external_id, external_url="x")
            raise AssertionError("expected DuplicateChangeRequest")
        except DuplicateChangeRequest:
            pass

        # A re-request refreshes the declared title/audience/mapping on
        # the SAME Promotion row (rather than only ever setting them once, at creation).
        store.update_declarations(
            p.id, resource_title="Verify v2",
            audience_spec={"principals": [{"principal": "ana@acme.com", "is_group": False}]},
            table_mapping={"dev_x.a.b": "prod_x.a.b"})
        refreshed = store.get_promotion(p.id)
        assert refreshed.resource_title == "Verify v2", "update_declarations: resource_title"
        assert refreshed.audience_spec["principals"], "update_declarations: audience jsonb round-trips"
        assert refreshed.table_mapping == {"dev_x.a.b": "prod_x.a.b"}, "update_declarations: table_mapping jsonb round-trips"

        s1 = store.append_snapshot(p.id, gate_conclusion="failure", gate_summary="1 blocker",
                                   findings=[{"rule_id": "AUDIENCE-01"}], eval={"status": "advisory"},
                                   timeline=[{"key": "review", "status": "fail"}])
        s2 = store.append_snapshot(p.id, gate_conclusion="success", gate_summary="clean",
                                   findings=[], eval={"status": "pass"}, timeline=[])
        assert [s.id for s in store.list_snapshots(p.id)] == [s1.id, s2.id], "snapshots retained+ordered"
        assert store.latest_snapshot(p.id).gate_conclusion == "success", "latest snapshot wins"
        assert store.latest_snapshot(p.id).findings == [], "jsonb round-trips (empty list)"

        e1 = store.append_audit_event(p.id, "requested", actor_app_email="verify@acme.com")
        e2 = store.append_audit_event(p.id, "pr_opened", actor_github_login="genie-promote-bot")
        e3 = store.append_audit_event(p.id, "merged", actor_github_login="PSPedro176")
        assert [e.seq for e in (e1, e2, e3)] == [1, 2, 3], "monotonic per-promotion seq"
        evs = store.list_audit_events(p.id)
        assert [e.event_type for e in evs] == ["requested", "pr_opened", "merged"], "ordered by seq"
        assert evs[0].actor_app_email == "verify@acme.com" and evs[2].actor_github_login == "PSPedro176"
        # Partial unique index: re-appending a milestone is a no-op (the concurrent-reconcile guard).
        assert store.append_audit_event(p.id, "merged", actor_github_login="x") is None, "dup milestone skipped"
        assert [e.event_type for e in store.list_audit_events(p.id)].count("merged") == 1, "no dup row"

        assert any(x.id == p.id for x in store.list_non_terminal()), "non-terminal listed (LB6)"
        store.update_cache(p.id, current_phase="deployed",
                           live_status={"phase": "deployed", "merged": True}, terminal=True)
        got = store.get_promotion(p.id)
        assert got.current_phase == "deployed" and got.terminal is True, "cache update"
        assert all(x.id != p.id for x in store.list_non_terminal()), "terminal NOT listed (LB6)"
        assert got.live_status == {"phase": "deployed", "merged": True}, "live_status jsonb round-trips"
        print("OK — migrations + full round-trip + append-only audit + cache all pass on real Lakebase")
    finally:
        _purge_test_rows(backend)
        # Drop the throwaway schema entirely (never the app's real genie_promote).
        if args.schema != "genie_promote":
            with backend._pool.connection() as conn, conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{args.schema}" CASCADE')
            print(f"dropped throwaway schema {args.schema!r}")
        else:
            print("cleaned up test rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())

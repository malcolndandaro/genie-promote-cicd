"""Offline contract tests for promotion_store (LB2) — InMemoryBackend, NO live Lakebase.

Asserts the store's external contract the rest of the app relies on: Promotion round-trip
(create/get/list/find-by-Change-Request), 1:1 Change Request, immutable review snapshots, audit trail
with monotonic per-promotion seq, status-cache update, role-scoped listing, and the hard-dependency
fail-fast at startup. The PgBackend SQL is proven separately against real Lakebase by
scripts/verify_lakebase_store.py (kept out of CI — 'no live Lakebase in CI').
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from promotion_store import (  # noqa: E402
    DuplicateChangeRequest, InMemoryBackend, LakebaseUnavailable, PromotionStore,
    build_store_from_env,
)


@pytest.fixture
def clock():
    """A deterministic, strictly-increasing UTC clock so created_at ordering is predictable."""
    state = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def tick():
        state["t"] += timedelta(seconds=1)
        return state["t"]

    return tick


@pytest.fixture
def store(clock):
    return PromotionStore(InMemoryBackend(), clock=clock)


def _mk(store, *, external_id=101, requester="ana@acme.com", resource_id="space-1"):
    return store.create_promotion(
        resource_id=resource_id, resource_kind="genie_space", resource_title="Recebíveis",
        requester_email=requester, branch="promote/recebiveis", current_phase="open",
        live_status={"phase": "open"}, change_provider="github", external_id=str(external_id),
        external_url=f"https://gh/change/{external_id}")


def test_promotion_round_trip(store):
    p = _mk(store)
    got = store.get_promotion(p.id)
    assert got is not None and got.id == p.id
    assert got.resource_id == "space-1" and got.external_id == "101" and got.terminal is False
    assert store.find_by_change_request("github", "101").id == p.id
    assert store.get_promotion("missing") is None


def test_one_to_one_change_request(store):
    _mk(store, external_id=101)
    with pytest.raises(DuplicateChangeRequest):
        _mk(store, external_id=101, resource_id="space-2")


def test_provider_neutral_change_request_round_trip(store):
    p = store.create_promotion(
        resource_id="space-1",
        resource_kind="genie_space",
        resource_title="Recebíveis",
        requester_email="ana@acme.com",
        branch="promote/recebiveis",
        current_phase="open",
        live_status=None,
        change_provider="github",
        external_id="101",
        external_url="https://gh/change/101",
        content_revision="b" * 64,
        engine_revision="a" * 40,
    )
    got = store.find_by_change_request("github", "101")
    assert got.id == p.id
    assert got.content_revision == "b" * 64
    assert got.engine_revision == "a" * 40


def test_child_writes_require_an_existing_promotion(store):
    # The fake mirrors the Postgres FK (REFERENCES promotions(id)) so an invalid id fails in CI too.
    with pytest.raises(ValueError):
        store.append_snapshot("ghost", gate_conclusion="success", gate_summary="", findings=[],
                              eval=None, timeline=[])
    with pytest.raises(ValueError):
        store.append_audit_event("ghost", "requested")


def test_list_promotions_role_scoped_and_newest_first(store):
    a1 = _mk(store, external_id=1, requester="ana@acme.com")
    b1 = _mk(store, external_id=2, requester="bob@acme.com")
    a2 = _mk(store, external_id=3, requester="ana@acme.com")
    # scope=mine: only the caller's, newest first
    ana = store.list_promotions("ana@acme.com")
    assert [p.id for p in ana] == [a2.id, a1.id]
    # scope=all (None): everyone, newest first
    every = store.list_promotions(None)
    assert [p.id for p in every] == [a2.id, b1.id, a1.id]


def test_snapshots_are_retained_and_latest_wins(store):
    p = _mk(store)
    s1 = store.append_snapshot(p.id, gate_conclusion="failure", gate_summary="1 blocker",
                               findings=[{"rule_id": "AUDIENCE-01"}], eval={"status": "advisory"},
                               timeline=[{"key": "review", "status": "fail"}])
    s2 = store.append_snapshot(p.id, gate_conclusion="success", gate_summary="clean",
                               findings=[], eval={"status": "pass"}, timeline=[])
    snaps = store.list_snapshots(p.id)
    assert [s.id for s in snaps] == [s1.id, s2.id]          # both retained, oldest->newest
    assert store.latest_snapshot(p.id).id == s2.id          # latest wins
    assert store.latest_snapshot(p.id).gate_conclusion == "success"
    assert store.latest_snapshot("nope") is None


def test_audit_trail_is_append_only_with_monotonic_seq(store):
    p = _mk(store)
    e1 = store.append_audit_event(p.id, "requested", actor_app_email="ana@acme.com")
    e2 = store.append_audit_event(p.id, "pr_opened", actor_github_login="genie-promote-bot")
    e3 = store.append_audit_event(p.id, "merged", actor_github_login="PSPedro176",
                                  github_event_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert [e.seq for e in (e1, e2, e3)] == [1, 2, 3]       # monotonic per promotion
    events = store.list_audit_events(p.id)
    assert [e.event_type for e in events] == ["requested", "pr_opened", "merged"]
    # governance attribution: github login authoritative, app email display-only (only on requested)
    assert events[0].actor_app_email == "ana@acme.com" and events[0].actor_github_login is None
    assert events[2].actor_github_login == "PSPedro176"
    # append-only: the store exposes no update/delete for events
    assert not any(hasattr(store, m) for m in ("update_audit_event", "delete_audit_event"))


def test_audit_seq_is_per_promotion(store):
    p1, p2 = _mk(store, external_id=1), _mk(store, external_id=2)
    store.append_audit_event(p1.id, "requested")
    store.append_audit_event(p2.id, "requested")
    store.append_audit_event(p1.id, "pr_opened")
    assert [e.seq for e in store.list_audit_events(p1.id)] == [1, 2]
    assert [e.seq for e in store.list_audit_events(p2.id)] == [1]


def test_unknown_event_type_rejected(store):
    p = _mk(store)
    with pytest.raises(ValueError):
        store.append_audit_event(p.id, "exploded")


def test_update_cache_refreshes_status(store):
    p = _mk(store)
    store.update_cache(p.id, current_phase="deployed",
                       live_status={"phase": "deployed", "merged": True}, terminal=True)
    got = store.get_promotion(p.id)
    assert got.current_phase == "deployed" and got.terminal is True
    assert got.live_status == {"phase": "deployed", "merged": True}
    assert got.last_reconciled_at is not None
    assert got.updated_at == got.last_reconciled_at  # one clock read -> the two timestamps agree


def test_deployment_attempt_upserts_latest_provider_evidence(store):
    p = _mk(store)
    base = {
        "attempt_id": "github:88:1", "run_attempt": 1,
        "revisions": {"content_revision": "b" * 64, "engine_revision": "a" * 40},
        "mutation_started": False, "completed_stages": ["preflight"],
        "current_stage": "bundle_deploy", "failed_stage": None,
        "target_ids": {}, "reason": None, "run_url": "https://gh/run/88",
        "terminal_state": "running",
    }
    first = store.upsert_deployment_attempt(p.id, base)
    assert first.external_run_id == "88" and first.terminal_state == "running"
    store.upsert_deployment_attempt(p.id, {
        **base, "mutation_started": True,
        "completed_stages": ["preflight", "bundle_deploy", "resolve_space"],
        "current_stage": "assert_app_manage", "failed_stage": "assert_app_manage",
        "target_ids": {"receivables": "space-1"}, "reason": "permission timeout",
        "terminal_state": "partial_failed",
    })
    attempts = store.list_deployment_attempts(p.id)
    assert len(attempts) == 1
    assert attempts[0].terminal_state == "partial_failed"
    assert attempts[0].mutation_started is True
    assert attempts[0].target_ids == {"receivables": "space-1"}


def test_migrate_delegates(store):
    store.migrate()
    assert store._b.migrated is True


# --- hard dependency (fail fast + loud) -------------------------------------


def test_build_store_returns_none_without_lakebase_env():
    # Local/test with no PGHOST: the app still runs (no DB bound) — not a hard failure.
    assert build_store_from_env(env={}) is None


def test_build_store_fails_fast_when_required_but_unbound():
    # APP_REQUIRE_STORE=1 (the deployed app sets it): a missing PGHOST means the `database` binding
    # was dropped/misconfigured — fail fast + loud rather than silently degrade (ADR-0005).
    with pytest.raises(LakebaseUnavailable) as ei:
        build_store_from_env(env={"APP_REQUIRE_STORE": "1"})
    assert "binding" in str(ei.value)


def test_build_store_fails_fast_when_lakebase_unreachable():
    def boom():
        raise ConnectionError("could not connect to host")

    with pytest.raises(LakebaseUnavailable) as ei:
        build_store_from_env(env={"PGHOST": "unreachable.example", "PGUSER": "sp"},
                             backend_factory=boom)
    msg = str(ei.value)
    assert "dependência obrigatória" in msg and "could not connect" in msg  # clear + root cause


def test_build_store_succeeds_with_a_working_backend():
    store = build_store_from_env(env={"PGHOST": "x", "PGUSER": "sp"},
                                 backend_factory=InMemoryBackend)
    assert isinstance(store, PromotionStore)
    # migrate() + healthcheck() ran on the backend
    assert store._b.migrated is True


# --- Required audience and optional table mapping persist with the Promotion ------------------

_AUDIENCE = {"principals": [{"principal": "data_analysts", "is_group": True}]}

_TABLE_MAPPING = {"dev_recebiveis.diamond.dim_cedente": "prod_recebiveis.diamond.dim_cedente_v2"}


def test_promotion_persists_and_round_trips_the_declared_table_mapping(store):
    p = store.create_promotion(
        resource_id="space-1", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@acme.com", change_provider="github", external_id="101",
        external_url="https://gh/change/101",
        branch="promote/recebiveis", current_phase="open", live_status=None,
        audience_spec=_AUDIENCE, table_mapping=_TABLE_MAPPING)
    assert p.audience_spec == _AUDIENCE
    assert p.table_mapping == _TABLE_MAPPING
    got = store.get_promotion(p.id)
    assert got.table_mapping == _TABLE_MAPPING  # survives the round trip through the backend


def test_promotion_table_mapping_defaults_to_none_when_not_declared(store):
    """A promotion with no declared table de-para (the plain dev_->prod_ default) persists cleanly
    with None — G7 is additive, not a breaking schema change for existing promotions."""
    p = _mk(store)
    assert p.table_mapping is None
    assert store.get_promotion(p.id).table_mapping is None


def test_declarations_survive_a_re_review_snapshot_without_being_overwritten(store):
    """A re-review snapshot does not clobber the Promotion declarations."""
    p = store.create_promotion(
        resource_id="space-1", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@acme.com", change_provider="github", external_id="101",
        external_url="https://gh/change/101",
        branch="promote/recebiveis", current_phase="open", live_status=None,
        audience_spec=_AUDIENCE, table_mapping=_TABLE_MAPPING)
    store.append_snapshot(p.id, gate_conclusion="failure", gate_summary="blocked",
                          findings=[], eval=None, timeline=[])
    store.touch(p.id)
    assert store.get_promotion(p.id).audience_spec == _AUDIENCE
    assert store.get_promotion(p.id).table_mapping == _TABLE_MAPPING


def test_update_declarations_refreshes_title_audience_and_table_mapping(store):
    """A re-request can change all declarations on an existing Promotion —
    unlike `create_promotion`, which only ever sets them once. `update_declarations` replaces all
    three outright (no merge), same as a re-request's own semantics: whatever the LATEST request
    declared is what's stored."""
    p = store.create_promotion(
        resource_id="space-1", resource_kind="genie_space", resource_title="Recebíveis v1",
        requester_email="ana@acme.com", change_provider="github", external_id="101",
        external_url="https://gh/change/101",
        branch="promote/recebiveis", current_phase="open", live_status=None,
        audience_spec=_AUDIENCE)
    new_audience = {"principals": [{"principal": "bob@acme.com", "is_group": False}]}
    store.update_declarations(p.id, resource_title="Recebíveis v2", audience_spec=new_audience,
                              table_mapping=_TABLE_MAPPING)
    got = store.get_promotion(p.id)
    assert got.resource_title == "Recebíveis v2"
    assert got.audience_spec == new_audience
    assert got.table_mapping == _TABLE_MAPPING


def test_update_declarations_can_clear_optional_mapping(store):
    """Declare-latest-wins clears an optional mapping while retaining the required audience."""
    p = store.create_promotion(
        resource_id="space-1", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@acme.com", change_provider="github", external_id="101",
        external_url="https://gh/change/101",
        branch="promote/recebiveis", current_phase="open", live_status=None,
        audience_spec=_AUDIENCE, table_mapping=_TABLE_MAPPING)
    store.update_declarations(
        p.id, resource_title=None, audience_spec=_AUDIENCE, table_mapping=None)
    got = store.get_promotion(p.id)
    assert got.resource_title is None
    assert got.audience_spec == _AUDIENCE
    assert got.table_mapping is None


def test_update_declarations_bumps_updated_at(store, clock):
    p = _mk(store)
    before = store.get_promotion(p.id).updated_at
    store.update_declarations(p.id, resource_title="x", audience_spec=_AUDIENCE, table_mapping=None)
    assert store.get_promotion(p.id).updated_at > before


def test_list_recent_audit_events_cross_promotion_newest_first_joined_bounded(store):
    # F4 review should-fix: the cross-Promotion audit is ONE joined query, newest-first, bounded.
    p1 = _mk(store, external_id=201, resource_id="space-A")
    p2 = _mk(store, external_id=202, resource_id="space-B")
    store.append_audit_event(p1.id, "requested", actor_app_email="ana@acme.com")   # oldest
    store.append_audit_event(p2.id, "requested", actor_app_email="bob@acme.com")
    store.append_audit_event(p1.id, "pr_opened", actor_github_login="genie-promote-bot")  # newest

    rows = store.list_recent_audit_events(limit=200)
    # spans BOTH promotions, newest-first (strictly-increasing clock)
    assert [r["event_type"] for r in rows] == ["pr_opened", "requested", "requested"]
    assert rows[0]["promotion_id"] == p1.id
    # each row is joined with its Promotion's resource fields
    assert rows[0]["resource_id"] == "space-A" and rows[0]["resource_title"] == "Recebíveis"
    assert {r["resource_id"] for r in rows} == {"space-A", "space-B"}
    # limit truncates the OLDEST, never the newest
    top1 = store.list_recent_audit_events(limit=1)
    assert len(top1) == 1 and top1[0]["event_type"] == "pr_opened"


def test_list_recent_audit_events_offset_pages_past_the_limit(store):
    # S4 (app-ux-overhaul, GR4): offset-based pagination on top of the existing limit.
    p = _mk(store, external_id=301)
    store.append_audit_event(p.id, "requested", actor_app_email="ana@acme.com")
    for i in range(3):
        store.append_audit_event(p.id, "re_reviewed", detail={"i": i})
    page1 = store.list_recent_audit_events(limit=2, offset=0)
    page2 = store.list_recent_audit_events(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert {r["seq"] for r in page1}.isdisjoint({r["seq"] for r in page2})


def test_list_recent_audit_events_filters_by_resource_id(store):
    p1 = _mk(store, external_id=302, resource_id="space-A")
    p2 = _mk(store, external_id=303, resource_id="space-B")
    store.append_audit_event(p1.id, "requested", actor_app_email="ana@acme.com")
    store.append_audit_event(p2.id, "requested", actor_app_email="ana@acme.com")
    rows = store.list_recent_audit_events(limit=200, resource_id="space-A")
    assert len(rows) == 1 and rows[0]["promotion_id"] == p1.id


def test_list_recent_audit_events_filters_by_actor_either_identity_field(store):
    p1 = _mk(store, external_id=304, requester="ana@acme.com")
    p2 = _mk(store, external_id=305, requester="bob@acme.com")
    store.append_audit_event(p1.id, "requested", actor_app_email="ana@acme.com")
    store.append_audit_event(p2.id, "requested", actor_app_email="bob@acme.com")
    store.append_audit_event(p1.id, "pr_review_approved", actor_github_login="genie-promote-bot")

    by_login = store.list_recent_audit_events(limit=200, actor="genie-promote-bot")
    assert len(by_login) == 1 and by_login[0]["actor_github_login"] == "genie-promote-bot"

    by_email = store.list_recent_audit_events(limit=200, actor="ana@acme.com")
    assert len(by_email) == 1 and by_email[0]["actor_app_email"] == "ana@acme.com"


def test_list_recent_audit_events_date_range(store, clock):
    p = _mk(store, external_id=306)
    store.append_audit_event(p.id, "requested", actor_app_email="ana@acme.com")
    all_rows = store.list_recent_audit_events(limit=200)
    occurred_at = all_rows[0]["occurred_at"]

    assert store.list_recent_audit_events(limit=200, after=occurred_at + timedelta(days=1)) == []
    assert len(store.list_recent_audit_events(limit=200, before=occurred_at + timedelta(days=1))) == 1
    assert len(store.list_recent_audit_events(limit=200, after=occurred_at - timedelta(days=1))) == 1


# --- rehydrate_events: standalone audit, NO FK to a Promotion ----------------------------------


def test_append_rehydrate_event_round_trips_with_no_promotion(store):
    # The whole point (F1 follow-up): this row exists precisely BECAUSE there's no Promotion to
    # attach to — inserting one must not require (or accept) a promotion_id at all.
    e = store.append_rehydrate_event(
        resource_id="prod-1", resource_title="Recebíveis", actor_email="ana@x", mode="create",
        dev_space_id="dev-1", detail={"acting_identity": "ana@x"})
    assert e.resource_id == "prod-1"
    assert e.resource_title == "Recebíveis"
    assert e.actor_email == "ana@x"
    assert e.mode == "create"
    assert e.dev_space_id == "dev-1"
    assert e.detail == {"acting_identity": "ana@x"}

    rows = store.list_rehydrate_events()
    assert len(rows) == 1 and rows[0].id == e.id


def test_append_rehydrate_event_requires_actor_email(store):
    with pytest.raises(ValueError):
        store.append_rehydrate_event(resource_id="prod-1", resource_title=None, actor_email="",
                                     mode="create", dev_space_id="dev-1")


def test_append_rehydrate_event_rejects_unknown_mode(store):
    with pytest.raises(ValueError):
        store.append_rehydrate_event(resource_id="prod-1", resource_title=None,
                                     actor_email="ana@x", mode="delete", dev_space_id="dev-1")


def test_list_rehydrate_events_filters_by_resource_and_orders_newest_first(store):
    store.append_rehydrate_event(resource_id="prod-A", resource_title="A", actor_email="ana@x",
                                 mode="create", dev_space_id="dev-A1")   # oldest
    store.append_rehydrate_event(resource_id="prod-B", resource_title="B", actor_email="bob@x",
                                 mode="create", dev_space_id="dev-B1")
    store.append_rehydrate_event(resource_id="prod-A", resource_title="A", actor_email="ana@x",
                                 mode="overwrite", dev_space_id="dev-A1")  # newest

    all_rows = store.list_rehydrate_events()
    assert [r.mode for r in all_rows] == ["overwrite", "create", "create"]  # newest-first

    a_only = store.list_rehydrate_events(resource_id="prod-A")
    assert len(a_only) == 2 and all(r.resource_id == "prod-A" for r in a_only)

    top1 = store.list_rehydrate_events(limit=1)
    assert len(top1) == 1 and top1[0].mode == "overwrite"  # limit truncates the OLDEST

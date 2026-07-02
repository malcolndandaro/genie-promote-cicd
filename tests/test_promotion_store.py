"""Offline contract tests for promotion_store (LB2) — InMemoryBackend, NO live Lakebase.

Asserts the store's external contract the rest of the app relies on: Promotion round-trip
(create/get/list/find-by-PR), 1:1-PR, immutable+retained review snapshots, append-only audit trail
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
    DuplicatePRNumber, InMemoryBackend, LakebaseUnavailable, PromotionStore, build_store_from_env,
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


def _mk(store, *, pr_number=101, requester="ana@acme.com", resource_id="space-1"):
    return store.create_promotion(
        resource_id=resource_id, resource_kind="genie_space", resource_title="Recebíveis",
        requester_email=requester, pr_number=pr_number, pr_url=f"https://gh/pr/{pr_number}",
        branch="promote/recebiveis", current_phase="open", live_status={"phase": "open"})


def test_promotion_round_trip(store):
    p = _mk(store)
    got = store.get_promotion(p.id)
    assert got is not None and got.id == p.id
    assert got.resource_id == "space-1" and got.pr_number == 101 and got.terminal is False
    assert store.find_by_pr(101).id == p.id
    assert store.get_promotion("missing") is None


def test_one_to_one_pr(store):
    _mk(store, pr_number=101)
    # A second promotion for the SAME PR violates the 1:1-PR rule. Both backends raise the SAME
    # type (DuplicatePRNumber) — Postgres maps its UNIQUE(pr_number) violation to it — so LB3 has
    # one exception to catch regardless of backend.
    with pytest.raises(DuplicatePRNumber):
        _mk(store, pr_number=101, resource_id="space-2")


def test_child_writes_require_an_existing_promotion(store):
    # The fake mirrors the Postgres FK (REFERENCES promotions(id)) so an invalid id fails in CI too.
    with pytest.raises(ValueError):
        store.append_snapshot("ghost", gate_conclusion="success", gate_summary="", findings=[],
                              eval=None, timeline=[])
    with pytest.raises(ValueError):
        store.append_audit_event("ghost", "requested")


def test_list_promotions_role_scoped_and_newest_first(store):
    a1 = _mk(store, pr_number=1, requester="ana@acme.com")
    b1 = _mk(store, pr_number=2, requester="bob@acme.com")
    a2 = _mk(store, pr_number=3, requester="ana@acme.com")
    # scope=mine: only the caller's, newest first
    ana = store.list_promotions("ana@acme.com")
    assert [p.id for p in ana] == [a2.id, a1.id]
    # scope=all (None): everyone, newest first
    every = store.list_promotions(None)
    assert [p.id for p in every] == [a2.id, b1.id, a1.id]


def test_snapshots_are_retained_and_latest_wins(store):
    p = _mk(store)
    s1 = store.append_snapshot(p.id, gate_conclusion="failure", gate_summary="1 blocker",
                               findings=[{"rule_id": "GRANT-01"}], eval={"status": "advisory"},
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
    p1, p2 = _mk(store, pr_number=1), _mk(store, pr_number=2)
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


# --- F2: the declared AccessSpec persists WITH the Promotion -----------------

_ACCESS_SPEC = {
    "space_permissions": [{"principal": "data_analysts", "is_group": True, "level": "CAN_RUN"}],
    "uc_principals": [{"principal": "ana@x.com", "is_group": False}],
}


def test_promotion_persists_and_round_trips_the_declared_access_spec(store):
    p = store.create_promotion(
        resource_id="space-1", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@acme.com", pr_number=101, pr_url="https://gh/pr/101",
        branch="promote/recebiveis", current_phase="open", live_status=None,
        access_spec=_ACCESS_SPEC)
    assert p.access_spec == _ACCESS_SPEC
    got = store.get_promotion(p.id)
    assert got.access_spec == _ACCESS_SPEC  # survives the round trip through the backend


def test_promotion_access_spec_defaults_to_none_when_not_declared(store):
    """A promotion with no declared AccessSpec (pre-F2 behavior) persists cleanly with None — F2
    is additive, not a breaking schema change for existing promotions."""
    p = _mk(store)
    assert p.access_spec is None
    assert store.get_promotion(p.id).access_spec is None


def test_access_spec_survives_a_re_review_snapshot_without_being_overwritten(store):
    """Appending a NEW review snapshot (a re-review) must not clobber the Promotion's already
    -declared AccessSpec — access_spec lives on the Promotion row, not the snapshot."""
    p = store.create_promotion(
        resource_id="space-1", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@acme.com", pr_number=101, pr_url="https://gh/pr/101",
        branch="promote/recebiveis", current_phase="open", live_status=None,
        access_spec=_ACCESS_SPEC)
    store.append_snapshot(p.id, gate_conclusion="failure", gate_summary="blocked",
                          findings=[], eval=None, timeline=[])
    store.touch(p.id)
    assert store.get_promotion(p.id).access_spec == _ACCESS_SPEC

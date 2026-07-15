"""Offline contract tests for access_request_store (F3) — InMemoryBackend, NO live Lakebase.

Mirrors tests/test_promotion_store.py's conventions: asserts the store's external contract —
create + persist, append-only audit with monotonic per-request seq, the SoD self-approval guard,
the requested->approved|denied->applied state machine (+ invalid-transition guards), and role-
scoped listing. The PgBackend SQL isn't proven here (no live Lakebase in CI, same as its sibling).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from access_request_store import (  # noqa: E402
    AccessRequestStore, InMemoryBackend, InvalidTransition, SelfApprovalError,
)


@pytest.fixture
def clock():
    state = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    def tick():
        state["t"] += timedelta(seconds=1)
        return state["t"]

    return tick


@pytest.fixture
def store(clock):
    return AccessRequestStore(InMemoryBackend(), clock=clock)


def _mk(store, *, requester="ana@acme.com", space_id="space-1", want_space_permission=True,
        want_uc_select=False):
    return store.create_request(
        space_id=space_id, space_title="Recebíveis", requester_email=requester,
        note="preciso para o fechamento mensal", want_space_permission=want_space_permission,
        space_permission_level="CAN_RUN", want_uc_select=want_uc_select)


def test_create_persists_and_audits_the_request(store):
    r = _mk(store)
    got = store.get_request(r.id)
    assert got is not None and got.state == "requested" and got.requester_email == "ana@acme.com"
    events = store.list_audit_events(r.id)
    assert [e.event_type for e in events] == ["requested"]
    assert events[0].actor_email == "ana@acme.com"  # the acting identity, never a header
    assert events[0].seq == 1


def test_create_requires_a_verified_requester_email(store):
    with pytest.raises(ValueError):
        store.create_request(space_id="s", space_title=None, requester_email="")


def test_create_requires_at_least_one_access_kind(store):
    with pytest.raises(ValueError):
        store.create_request(space_id="s", space_title=None, requester_email="ana@x",
                             want_space_permission=False, want_uc_select=False)


def test_get_request_missing_returns_none(store):
    assert store.get_request("nope") is None


def test_child_writes_require_an_existing_request(store):
    with pytest.raises(ValueError):
        store._append_event("ghost", "requested", actor_email="a@x")


# --- SoD: approver != requester, enforced server-side on the STORE'S OWN data ---


def test_self_approval_is_rejected(store):
    r = _mk(store, requester="ana@acme.com")
    with pytest.raises(SelfApprovalError):
        store.decide(r.id, approve=True, approver_email="ana@acme.com")
    # rejection must not mutate state or leave a decision event
    assert store.get_request(r.id).state == "requested"
    assert [e.event_type for e in store.list_audit_events(r.id)] == ["requested"]


def test_self_approval_rejected_case_insensitively(store):
    r = _mk(store, requester="Ana@Acme.com")
    with pytest.raises(SelfApprovalError):
        store.decide(r.id, approve=True, approver_email="ana@acme.com")


def test_self_denial_is_also_rejected(store):
    # SoD applies to BOTH decisions, not just approval (a requester can't deny their own to game
    # a resubmit either).
    r = _mk(store, requester="ana@acme.com")
    with pytest.raises(SelfApprovalError):
        store.decide(r.id, approve=False, approver_email="ana@acme.com")


def test_distinct_approver_can_approve(store):
    r = _mk(store, requester="ana@acme.com")
    decided = store.decide(r.id, approve=True, approver_email="pedro@acme.com", decision_note="ok")
    assert decided.state == "approved" and decided.decided_by == "pedro@acme.com"
    events = store.list_audit_events(r.id)
    assert [e.event_type for e in events] == ["requested", "approved"]
    assert events[1].actor_email == "pedro@acme.com" and events[1].detail == {"note": "ok"}


def test_distinct_approver_can_deny(store):
    r = _mk(store, requester="ana@acme.com")
    decided = store.decide(r.id, approve=False, approver_email="pedro@acme.com", decision_note="fora do orçamento")
    assert decided.state == "denied"
    assert [e.event_type for e in store.list_audit_events(r.id)] == ["requested", "denied"]


# --- S5 (app-ux-overhaul, D8): a denial REQUIRES a reason; approval does not -------------------


def test_deny_without_a_reason_is_rejected(store):
    r = _mk(store, requester="ana@acme.com")
    with pytest.raises(ValueError):
        store.decide(r.id, approve=False, approver_email="pedro@acme.com")
    with pytest.raises(ValueError):
        store.decide(r.id, approve=False, approver_email="pedro@acme.com", decision_note="   ")  # whitespace-only


def test_approve_without_a_reason_is_fine(store):
    r = _mk(store, requester="ana@acme.com")
    decided = store.decide(r.id, approve=True, approver_email="pedro@acme.com")
    assert decided.state == "approved"


# --- state machine: requested -> approved|denied -> applied ---


def test_cannot_decide_a_request_twice(store):
    r = _mk(store)
    store.decide(r.id, approve=True, approver_email="pedro@x")
    with pytest.raises(InvalidTransition):
        store.decide(r.id, approve=True, approver_email="pedro@x")
    with pytest.raises(InvalidTransition):
        store.decide(r.id, approve=False, approver_email="pedro@x")


def test_cannot_decide_a_denied_request(store):
    r = _mk(store)
    store.decide(r.id, approve=False, approver_email="pedro@x", decision_note="não autorizado")
    with pytest.raises(InvalidTransition):
        store.decide(r.id, approve=True, approver_email="pedro@x")


def test_mark_applied_requires_approved_state(store):
    r = _mk(store)
    with pytest.raises(InvalidTransition):
        store.mark_applied(r.id, actor_email="pedro@x", pr_number=1, pr_url="https://gh/pr/1")


def test_mark_applied_records_pr_and_audits(store):
    r = _mk(store)
    store.decide(r.id, approve=True, approver_email="pedro@x")
    applied = store.mark_applied(r.id, actor_email="pedro@x", pr_number=42,
                                 pr_url="https://gh/pr/42", detail={"branch": "access/space-1"})
    assert applied.state == "applied" and applied.pr_number == 42
    events = store.list_audit_events(r.id)
    assert [e.event_type for e in events] == ["requested", "approved", "applied"]
    assert events[2].actor_email == "pedro@x"
    assert events[2].detail["pr_number"] == 42 and events[2].detail["branch"] == "access/space-1"


def test_mark_apply_failed_keeps_approved_state_and_audits_reason(store):
    r = _mk(store)
    store.decide(r.id, approve=True, approver_email="pedro@x")
    store.mark_apply_failed(r.id, actor_email="pedro@x", reason="GitHub 503")
    got = store.get_request(r.id)
    assert got.state == "approved"  # NOT silently marked applied/denied
    events = store.list_audit_events(r.id)
    assert [e.event_type for e in events] == ["requested", "approved", "apply_failed"]
    assert events[2].detail == {"reason": "GitHub 503"}


# --- listing: requester status view + approver pending queue ---


def test_list_requests_scoped_to_requester_newest_first(store):
    a1 = _mk(store, requester="ana@x")
    _mk(store, requester="bob@x")
    a2 = _mk(store, requester="ana@x")
    mine = store.list_requests(requester_email="ana@x")
    assert [r.id for r in mine] == [a2.id, a1.id]


def test_list_requests_filtered_by_state_is_the_approver_queue(store):
    r1 = _mk(store, requester="ana@x")
    r2 = _mk(store, requester="bob@x")
    store.decide(r1.id, approve=True, approver_email="pedro@x")
    pending = store.list_requests(state="requested")
    assert [r.id for r in pending] == [r2.id]


def test_list_requests_all_requesters_when_unscoped(store):
    _mk(store, requester="ana@x")
    _mk(store, requester="bob@x")
    assert len(store.list_requests()) == 2

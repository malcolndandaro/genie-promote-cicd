"""Offline tests for reconcile (LB4) — fake get_status + a real in-memory store, no live GitHub/DB.

Asserts: the exact new audit events per transition with GitHub-sourced identities + timestamps;
idempotency (re-running appends nothing); backfill of a multi-step jump (open -> deployed); the
status cache update; reflect-never-assert (an unreached milestone is never recorded).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import reconcile as reconcile_mod  # noqa: E402
from promotion_store import InMemoryBackend, PromotionStore  # noqa: E402


def _store():
    s = PromotionStore(InMemoryBackend())
    p = s.create_promotion(resource_id="sp1", resource_kind="genie_space", resource_title="Receb.",
                           requester_email="ana@x", pr_number=6, pr_url="https://gh/pr/6",
                           branch="promote/recebiveis", current_phase="open", live_status=None)
    # The app writes `requested` at promote time (LB3); reconcile must not duplicate it.
    s.append_audit_event(p.id, "requested", actor_app_email="ana@x")
    return s, p


def _status(phase, *, pr_state="open", merged=False, checks="pending",
            review_decision="review_required", deploy=None):
    return {"pr_state": pr_state, "merged": merged, "checks": checks,
            "review_decision": review_decision,
            "deploy": deploy or {"status": "none", "conclusion": None,
                                 "waiting_approval": False, "approver": None},
            "pr_url": "https://gh/pr/6", "phase": phase}


class _FakeGitHub:
    def __init__(self, facts=None):
        self.facts = facts or {}
        self.calls = 0

    def audit_facts(self, number):
        self.calls += 1
        return self.facts


def _factory(fake):
    return lambda: fake


def test_first_reconcile_open_pr_appends_pr_opened_only_and_caches():
    s, p = _store()
    fake = _FakeGitHub()
    appended = reconcile_mod.reconcile(s, p, _status("checks_running"), _factory(fake))
    assert appended == ["pr_opened"]
    assert [e.event_type for e in s.list_audit_events(p.id)] == ["requested", "pr_opened"]
    cached = s.get_promotion(p.id)
    assert cached.current_phase == "checks_running" and cached.live_status["phase"] == "checks_running"
    assert fake.calls == 0  # no identity fetch needed for pr_opened (the hot poll pays nothing)


def test_idempotent_no_new_events_on_unchanged_status():
    s, p = _store()
    st = _status("checks_running")
    reconcile_mod.reconcile(s, p, st, _factory(_FakeGitHub()))
    appended = reconcile_mod.reconcile(s, p, st, _factory(_FakeGitHub()))
    assert appended == []  # nothing new
    assert [e.event_type for e in s.list_audit_events(p.id)] == ["requested", "pr_opened"]


def test_pr_review_approved_carries_github_identity_and_timestamp():
    s, p = _store()
    fake = _FakeGitHub({"review_approver": "PSPedro176", "review_approved_at": "2026-06-26T12:00:00Z"})
    appended = reconcile_mod.reconcile(
        s, p, _status("open", review_decision="approved"), _factory(fake))
    assert "pr_review_approved" in appended and fake.calls == 1
    ev = next(e for e in s.list_audit_events(p.id) if e.event_type == "pr_review_approved")
    assert ev.actor_github_login == "PSPedro176"  # authoritative GitHub identity
    assert ev.actor_app_email is None  # governance attribution is GitHub, not the OBO email
    assert ev.github_event_at == datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)


def test_deploy_in_progress_does_not_record_deploy_approved_yet():
    # The gate is released + the deploy is running, but the approver isn't known until it COMPLETES,
    # so deploy_approved is NOT recorded prematurely (it would otherwise be stuck with a null actor).
    s, p = _store()
    reconcile_mod.reconcile(s, p, _status("merged", merged=True, checks="success",
                            review_decision="approved"), _factory(_FakeGitHub({"merged_by": "x"})))
    deploy = {"status": "in_progress", "conclusion": None, "waiting_approval": False, "approver": None}
    appended = reconcile_mod.reconcile(s, p, _status("deploying", merged=True, deploy=deploy),
                                       _factory(_FakeGitHub()))
    assert "deploy_approved" not in appended
    assert all(e.event_type != "deploy_approved" for e in s.list_audit_events(p.id))


def test_deploy_approved_recorded_on_completion_with_approver():
    s, p = _store()
    reconcile_mod.reconcile(s, p, _status("merged", merged=True, checks="success",
                            review_decision="approved"), _factory(_FakeGitHub({"merged_by": "x"})))
    fake = _FakeGitHub()
    deploy = {"status": "completed", "conclusion": "success", "waiting_approval": False, "approver": "PSPedro176"}
    appended = reconcile_mod.reconcile(s, p, _status("deployed", merged=True, deploy=deploy), _factory(fake))
    assert "deploy_approved" in appended and "deployed" in appended
    ev = next(e for e in s.list_audit_events(p.id) if e.event_type == "deploy_approved")
    assert ev.actor_github_login == "PSPedro176"  # the Steward's identity, captured at completion
    assert fake.calls == 0  # deploy_approved/deployed need no audit_facts fetch


def test_backfills_multi_step_jump_open_to_deployed():
    # An overnight merge+deploy with no one watching: one reconcile must emit every missed milestone.
    s, p = _store()
    fake = _FakeGitHub({"merged_by": "PSPedro176", "merged_at": "2026-06-26T13:00:00Z",
                        "review_approver": "PSPedro176", "review_approved_at": "2026-06-26T12:00:00Z"})
    deploy = {"status": "completed", "conclusion": "success", "waiting_approval": False, "approver": "PSPedro176"}
    appended = reconcile_mod.reconcile(
        s, p, _status("deployed", merged=True, checks="success",
                      review_decision="approved", deploy=deploy), _factory(fake))
    assert appended == ["pr_opened", "pr_review_approved", "merged", "deploy_approved", "deployed"]
    types = [e.event_type for e in s.list_audit_events(p.id)]
    assert types == ["requested", "pr_opened", "pr_review_approved", "merged", "deploy_approved", "deployed"]
    merged = next(e for e in s.list_audit_events(p.id) if e.event_type == "merged")
    assert merged.actor_github_login == "PSPedro176"
    assert merged.github_event_at == datetime(2026, 6, 26, 13, 0, tzinfo=timezone.utc)
    assert s.get_promotion(p.id).terminal is True  # deployed is terminal


def test_deploy_failed_records_failed_and_is_terminal():
    s, p = _store()
    deploy = {"status": "completed", "conclusion": "failure", "waiting_approval": False, "approver": "PSPedro176"}
    appended = reconcile_mod.reconcile(
        s, p, _status("deploy_failed", merged=True, checks="success",
                      review_decision="approved", deploy=deploy),
        _factory(_FakeGitHub({"merged_by": "PSPedro176"})))
    assert "failed" in appended and "deployed" not in appended
    # deploy_approved is recorded with the Steward identity even on a failed deploy.
    ev = next(e for e in s.list_audit_events(p.id) if e.event_type == "deploy_approved")
    assert ev.actor_github_login == "PSPedro176"
    # Terminal: the LB6 scheduler must stop visiting a failed deploy (no infinite re-poll loop).
    assert s.get_promotion(p.id).terminal is True


def test_reconcile_mirrors_partial_attempt_without_asserting_deployed():
    s, p = _store()
    attempt = {
        "attempt_id": "github:8:1", "run_attempt": 1,
        "revisions": {"content_revision": "b" * 64, "engine_revision": "a" * 40},
        "mutation_started": True, "completed_stages": ["preflight", "bundle_deploy"],
        "current_stage": "resolve_space", "failed_stage": "resolve_space",
        "target_ids": {}, "reason": "space not visible", "run_url": "https://gh/run/8",
        "terminal_state": "partial_failed",
    }
    deploy = {"status": "completed", "conclusion": "failure", "waiting_approval": False,
              "approver": "PSPedro176", "attempt": attempt}
    reconcile_mod.reconcile(
        s, p, _status("deploy_failed", merged=True, checks="success",
                      review_decision="approved", deploy=deploy),
        _factory(_FakeGitHub({"merged_by": "PSPedro176"})))
    mirrored = s.list_deployment_attempts(p.id)
    assert len(mirrored) == 1 and mirrored[0].terminal_state == "partial_failed"
    types = [event.event_type for event in s.list_audit_events(p.id)]
    assert "failed" in types and "deployed" not in types


def test_closed_pr_records_closed_event_and_is_terminal():
    s, p = _store()
    appended = reconcile_mod.reconcile(
        s, p, _status("closed", pr_state="closed"), _factory(_FakeGitHub()))
    assert "closed" in appended
    assert s.get_promotion(p.id).terminal is True


def test_concurrent_double_reconcile_does_not_duplicate_an_event():
    # The dedup safety net (partial unique index, mirrored in the fake): re-appending a milestone
    # that already exists is a no-op, so two racing reconciles can't double-record an event_type.
    s, p = _store()
    st = _status("checks_running")
    reconcile_mod.reconcile(s, p, st, _factory(_FakeGitHub()))
    # Simulate a second reconcile that recomputed the same to_append before the first committed.
    assert s.append_audit_event(p.id, "pr_opened") is None  # skipped — already recorded
    assert [e.event_type for e in s.list_audit_events(p.id)].count("pr_opened") == 1


def test_reflect_never_assert_unreached_milestones_not_recorded():
    s, p = _store()
    reconcile_mod.reconcile(s, p, _status("checks_running"), _factory(_FakeGitHub()))
    types = {e.event_type for e in s.list_audit_events(p.id)}
    assert "merged" not in types and "deployed" not in types  # never asserted ahead of GitHub


# --- LB6: the scheduled reconciler (audit completeness when no one is viewing) ---


class _SweepGitHub:
    """A bot whose get_status is keyed by PR number, so reconcile_all can sweep several promotions."""
    def __init__(self, status_by_pr, facts=None):
        self.status_by_pr = status_by_pr
        self.facts = facts or {}
        self.status_calls = 0

    def get_status(self, number, approved_revisions=None):
        self.status_calls += 1
        self.approved_revisions = approved_revisions
        return self.status_by_pr[number]

    def audit_facts(self, number):
        return self.facts


def test_reconciler_records_an_unviewed_overnight_merge_to_deploy():
    # No browser was open; the scheduled sweep must backfill the whole jump from the live status.
    s = PromotionStore(InMemoryBackend())
    p = s.create_promotion(resource_id="sp1", resource_kind="genie_space", resource_title="R",
                           requester_email="ana@x", pr_number=6, pr_url="u", branch="b",
                           current_phase="open", live_status=None)
    s.append_audit_event(p.id, "requested", actor_app_email="ana@x")
    deploy = {"status": "completed", "conclusion": "success", "waiting_approval": False, "approver": "PSPedro176"}
    gh = _SweepGitHub({6: _status("deployed", merged=True, checks="success",
                                  review_decision="approved", deploy=deploy)},
                      facts={"merged_by": "PSPedro176", "review_approver": "PSPedro176"})
    result = reconcile_mod.reconcile_all(s, lambda: gh)
    assert result["checked"] == 1 and result["transitioned"][0]["pr_number"] == 6
    types = [e.event_type for e in s.list_audit_events(p.id)]
    assert types == ["requested", "pr_opened", "pr_review_approved", "merged", "deploy_approved", "deployed"]
    assert s.get_promotion(p.id).terminal is True  # now terminal -> won't be swept again


def test_reconciler_skips_terminal_and_is_idempotent():
    s = PromotionStore(InMemoryBackend())
    # A terminal promotion (deployed) is never swept.
    term = s.create_promotion(resource_id="sp0", resource_kind="genie_space", resource_title="T",
                              requester_email="ana@x", pr_number=1, pr_url="u", branch="b",
                              current_phase="deployed", live_status=None)
    s.update_cache(term.id, current_phase="deployed", live_status={"phase": "deployed"}, terminal=True)
    # A non-terminal one.
    p = s.create_promotion(resource_id="sp1", resource_kind="genie_space", resource_title="R",
                           requester_email="ana@x", pr_number=6, pr_url="u", branch="b",
                           current_phase="open", live_status=None)
    s.append_audit_event(p.id, "requested")
    gh = _SweepGitHub({6: _status("checks_running")})  # only PR 6 is queried (terminal is skipped)
    r1 = reconcile_mod.reconcile_all(s, lambda: gh)
    assert r1["checked"] == 1 and gh.status_calls == 1  # terminal one never hit GitHub
    assert [e.event_type for e in s.list_audit_events(p.id)] == ["requested", "pr_opened"]
    # Idempotent: a second sweep with the same status appends nothing.
    r2 = reconcile_mod.reconcile_all(s, lambda: gh)
    assert r2["transitioned"] == []
    assert [e.event_type for e in s.list_audit_events(p.id)] == ["requested", "pr_opened"]


def test_reconciler_no_promotions_is_a_noop():
    s = PromotionStore(InMemoryBackend())
    assert reconcile_mod.reconcile_all(s, lambda: _SweepGitHub({})) == {"checked": 0, "transitioned": []}

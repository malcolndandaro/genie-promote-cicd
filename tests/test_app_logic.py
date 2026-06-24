"""Unit tests for the App backend pure helpers (S8-S11). No Streamlit/Databricks."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import app_logic  # noqa: E402


def test_build_timeline_clean_pending_approval():
    tl = app_logic.build_timeline(checks_ok=True,
                                  gate={"conclusion": "success"},
                                  eval_res={"status": "advisory"},
                                  approved=False, deployed=False)
    by = {t["key"]: t["status"] for t in tl}
    assert by["checks"] == "pass" and by["review"] == "pass"
    assert by["approval"] == "running" and by["deploy"] == "pending"


def test_build_timeline_blocked_review():
    tl = app_logic.build_timeline(True, {"conclusion": "failure"}, {"status": "advisory"}, False, False)
    by = {t["key"]: t["status"] for t in tl}
    assert by["review"] == "fail"


def test_build_timeline_failed_checks():
    tl = app_logic.build_timeline(False, {"conclusion": "failure"}, {"status": "block"}, False, False)
    by = {t["key"]: t["status"] for t in tl}
    assert by["checks"] == "fail" and by["eval"] == "fail"


def test_sod_author_cannot_approve():
    ok, reason = app_logic.can_approve("author", "malcoln@x", "malcoln@x")
    assert not ok and "Steward" in reason


def test_sod_requester_cannot_self_approve_even_as_steward():
    ok, reason = app_logic.can_approve("steward", "malcoln@x", "malcoln@x")
    assert not ok and "Segregação" in reason


def test_sod_distinct_steward_can_approve():
    ok, _ = app_logic.can_approve("steward", "malcoln@x", "pedro@x")
    assert ok is True

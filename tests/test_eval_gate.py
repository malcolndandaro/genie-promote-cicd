"""Unit tests for the eval-run gate decision (S7). Pure — no Databricks."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import eval_gate  # noqa: E402


def test_all_pass_is_pass():
    d = eval_gate.decide_eval([{"passed": True}, {"passed": True}, {"passed": True}], threshold=0.8)
    assert d["status"] == "pass" and d["pass_rate"] == 1.0


def test_below_threshold_blocks():
    d = eval_gate.decide_eval([{"passed": True}, {"passed": False}, {"passed": False}], threshold=0.8)
    assert d["status"] == "block" and d["n"] == 3


def test_at_threshold_passes():
    d = eval_gate.decide_eval([{"passed": True}] * 4 + [{"passed": False}], threshold=0.8)
    assert d["status"] == "pass" and abs(d["pass_rate"] - 0.8) < 1e-9


def test_status_token_counts_as_pass():
    d = eval_gate.decide_eval([{"status": "PASSED"}, {"status": "passed"}], threshold=0.8)
    assert d["status"] == "pass"


def test_empty_results_advisory():
    assert eval_gate.decide_eval([], threshold=0.8)["status"] == "advisory"


def test_unavailable_advisory_not_block():
    d = eval_gate.decide_eval(None, available=False, reason="sem benchmarks")
    assert d["status"] == "advisory" and "não bloqueia" in d["summary"]


def test_passed_tolerant_helper():
    assert eval_gate._passed({"is_correct": True}) is True
    assert eval_gate._passed({"status": "FAIL"}) is False
    assert eval_gate._passed("nope") is False

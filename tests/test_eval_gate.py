"""Unit tests for the eval-run gate decision (S7) and the REST/SDK gate (W2). Pure — no
Databricks; the REST gate is exercised against a fake client (SimpleNamespace), never a network
call."""
import os
import sys
from types import SimpleNamespace as NS

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


# --- run_eval_gate_rest (W2): typed Genie eval-run SDK, no `databricks` CLI ---------------------
#
# Verified live (read-only probes, dev demo space, 24 benchmark questions): `genie_create_eval_run`
# returns a `GenieEvalRunResponse` immediately; `genie_get_eval_run` is the poll shape; a DONE run
# carries num_correct/num_done/num_needs_review/num_questions. The fake below mimics that shape
# with SimpleNamespace — no network, no real client.

class _FakeGenieEvalAPI:
    """Fake `client.genie`. `poll_sequence` is popped in order by successive
    `genie_get_eval_run` calls, so a test can script a create -> RUNNING -> ... -> DONE sequence."""

    def __init__(self, created, poll_sequence=(), create_error=None, poll_error=None):
        self._created = created
        self._poll_sequence = list(poll_sequence)
        self._create_error = create_error
        self._poll_error = poll_error
        self.create_calls = 0
        self.poll_calls = 0

    def genie_create_eval_run(self, space_id):
        self.create_calls += 1
        if self._create_error:
            raise self._create_error
        return self._created

    def genie_get_eval_run(self, space_id, eval_run_id):
        self.poll_calls += 1
        if self._poll_error:
            raise self._poll_error
        return self._poll_sequence.pop(0)


def _client(genie_api):
    return NS(genie=genie_api)


def _run(status, **kw):
    """A fake `GenieEvalRunResponse` — `status` may be a plain string or an enum-like object
    exposing `.value` (mirrors the real SDK's `EvaluationStatusType`)."""
    return NS(eval_run_id="run1", eval_run_status=status,
              num_correct=kw.get("num_correct"), num_done=kw.get("num_done"),
              num_needs_review=kw.get("num_needs_review"), num_questions=kw.get("num_questions"))


def test_rest_gate_immediate_done_pass():
    api = _FakeGenieEvalAPI(_run("DONE", num_correct=4, num_done=5, num_questions=5))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "pass" and abs(d["pass_rate"] - 0.8) < 1e-9
    assert api.poll_calls == 0  # already terminal on create — never polled


def test_rest_gate_immediate_done_block():
    api = _FakeGenieEvalAPI(_run("DONE", num_correct=1, num_done=5, num_questions=5))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "block" and d["n"] == 5


def test_rest_gate_polls_until_terminal():
    api = _FakeGenieEvalAPI(
        _run("RUNNING", num_correct=0, num_done=0, num_questions=2),
        poll_sequence=[_run("RUNNING", num_correct=1, num_done=1, num_questions=2),
                       _run("DONE", num_correct=2, num_done=2, num_questions=2)])
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8,
                                     wait_seconds=10, poll_interval=0)
    assert d["status"] == "pass" and d["n"] == 2
    assert api.poll_calls == 2


def test_rest_gate_budget_exhausted_is_advisory_not_timeout():
    api = _FakeGenieEvalAPI(_run("RUNNING", num_questions=24))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), wait_seconds=0, poll_interval=0)
    assert d["status"] == "advisory" and "não aguardado" in d["summary"]
    assert api.poll_calls == 0  # zero budget -> never even polled once


def test_rest_gate_create_error_degrades_to_advisory():
    api = _FakeGenieEvalAPI(None, create_error=RuntimeError("boom"))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api))
    assert d["status"] == "advisory"


def test_rest_gate_no_benchmarks_reason_classified():
    api = _FakeGenieEvalAPI(None, create_error=RuntimeError("No benchmark questions configured"))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api))
    assert d["status"] == "advisory" and eval_gate._NO_BENCH in d["summary"]


def test_rest_gate_poll_error_degrades_to_advisory():
    api = _FakeGenieEvalAPI(_run("RUNNING", num_questions=2), poll_error=RuntimeError("network blip"))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), wait_seconds=10, poll_interval=0)
    assert d["status"] == "advisory"


def test_rest_gate_non_done_terminal_status_degrades_to_advisory():
    api = _FakeGenieEvalAPI(_run("EVALUATION_FAILED", num_questions=2))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api))
    assert d["status"] == "advisory"


def test_rest_gate_zero_done_is_advisory_no_benchmarks():
    api = _FakeGenieEvalAPI(_run("DONE", num_correct=0, num_done=0, num_questions=0))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api))
    assert d["status"] == "advisory" and "benchmark" in d["summary"].lower()


def test_rest_gate_needs_review_counts_as_not_passed():
    # 2 correct + 1 needs_review + 1 plainly incorrect, out of num_done=4 -> only 2/4 "passed".
    api = _FakeGenieEvalAPI(_run("DONE", num_correct=2, num_done=4, num_needs_review=1, num_questions=4))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "block" and abs(d["pass_rate"] - 0.5) < 1e-9


def test_rest_gate_status_enum_like_object_normalizes_via_value():
    status = NS(value="DONE")  # mimics the real SDK's EvaluationStatusType enum
    api = _FakeGenieEvalAPI(_run(status, num_correct=5, num_done=5, num_questions=5))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "pass"


def test_rest_gate_uses_default_wait_seconds_when_not_specified(monkeypatch):
    monkeypatch.setattr(eval_gate, "_DEFAULT_WAIT_SECONDS", 0.0)
    api = _FakeGenieEvalAPI(_run("RUNNING", num_questions=2))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api))  # no wait_seconds -> uses the default
    assert d["status"] == "advisory" and api.poll_calls == 0

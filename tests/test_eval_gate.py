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


# --- W3: per-question results (real genie_list_eval_results/genie_get_eval_result_details) -----
#
# Verified live (read-only probe, dev demo space, run 01f17f81ac4d1417aff1941f4d72dead): a list
# item's `question`/`result_id` + a per-result `genie_get_eval_result_details.assessment`
# ('GOOD'/'BAD'/'NEEDS_REVIEW') — the fakes below mimic that exact shape.

def _list_page(results, next_page_token=None):
    return NS(eval_results=results, next_page_token=next_page_token)


def _result(result_id, question):
    return NS(result_id=result_id, question=question)


def _details(assessment):
    """`assessment` may be a plain string or an enum-like object exposing `.value` (mirrors the
    real SDK's `GenieEvalAssessment`)."""
    return NS(assessment=assessment)


class _FakeGenieEvalAPIWithResults(_FakeGenieEvalAPI):
    """Extends the counters-only fake with the per-question endpoints (W3). `pages` is a list of
    `_list_page(...)`, consumed in order across successive `genie_list_eval_results` calls; `details`
    maps `result_id -> _details(...)`."""

    def __init__(self, created, pages=(), details=None, **kw):
        super().__init__(created, **kw)
        self._pages = list(pages)
        self._details = details or {}
        self.list_calls = 0
        self.detail_calls = 0

    def genie_list_eval_results(self, space_id, eval_run_id, *, page_size=None, page_token=None):
        self.list_calls += 1
        return self._pages.pop(0)

    def genie_get_eval_result_details(self, space_id, eval_run_id, result_id):
        self.detail_calls += 1
        return self._details[result_id]


def test_rest_gate_fetches_real_per_question_results():
    api = _FakeGenieEvalAPIWithResults(
        _run("DONE", num_correct=1, num_done=2, num_questions=2),
        pages=[_list_page([_result("r1", "Quantos clientes?"), _result("r2", "Qual o volume?")])],
        details={"r1": _details("GOOD"), "r2": _details("BAD")},
    )
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "block" and d["n"] == 2 and d["n_correct"] == 1 and d["n_needs_review"] == 0
    assert d["run_id"] == "run1"
    # failures first
    assert [q["status"] for q in d["questions"]] == ["incorrect", "correct"]
    assert d["questions"][0]["question"] == "Qual o volume?"


def test_rest_gate_needs_review_question_counted_and_sorted_after_incorrect():
    api = _FakeGenieEvalAPIWithResults(
        _run("DONE", num_correct=1, num_done=3, num_needs_review=1, num_questions=3),
        pages=[_list_page([_result("r1", "A?"), _result("r2", "B?"), _result("r3", "C?")])],
        details={"r1": _details("GOOD"), "r2": _details("BAD"), "r3": _details("NEEDS_REVIEW")},
    )
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "block" and d["n_correct"] == 1 and d["n_needs_review"] == 1
    assert [q["status"] for q in d["questions"]] == ["incorrect", "needs_review", "correct"]


def test_rest_gate_pass_summary_mentions_n_and_rate():
    api = _FakeGenieEvalAPIWithResults(
        _run("DONE", num_correct=2, num_done=2, num_questions=2),
        pages=[_list_page([_result("r1", "A?"), _result("r2", "B?")])],
        details={"r1": _details("GOOD"), "r2": _details("GOOD")},
    )
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "pass"
    assert "2" in d["summary"] and "acima do limiar" in d["summary"]


def test_rest_gate_paginates_across_multiple_pages():
    api = _FakeGenieEvalAPIWithResults(
        _run("DONE", num_correct=2, num_done=2, num_questions=2),
        pages=[
            _list_page([_result("r1", "A?")], next_page_token="tok2"),
            _list_page([_result("r2", "B?")]),
        ],
        details={"r1": _details("GOOD"), "r2": _details("GOOD")},
    )
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "pass" and d["n"] == 2 and api.list_calls == 2


def test_rest_gate_assessment_enum_like_object_normalizes_via_value():
    status = NS(value="BAD")  # mimics the real SDK's GenieEvalAssessment enum
    api = _FakeGenieEvalAPIWithResults(
        _run("DONE", num_correct=0, num_done=1, num_questions=1),
        pages=[_list_page([_result("r1", "A?")])],
        details={"r1": _details(status)},
    )
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["questions"][0]["status"] == "incorrect"


def test_rest_gate_results_fetch_failure_falls_back_to_counters_only():
    # genie_list_eval_results raises -> degrades to the ORIGINAL synthetic-counters shape, no
    # `questions` key, exactly like before W3.
    class _BrokenList(_FakeGenieEvalAPIWithResults):
        def genie_list_eval_results(self, *a, **kw):
            raise RuntimeError("network blip")

    api = _BrokenList(_run("DONE", num_correct=4, num_done=4, num_questions=4))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    assert d["status"] == "pass" and d["n"] == 4
    assert "questions" not in d


def test_rest_gate_detail_lookup_failure_skips_that_question_not_whole_run():
    class _OneBadDetail(_FakeGenieEvalAPIWithResults):
        def genie_get_eval_result_details(self, space_id, eval_run_id, result_id):
            if result_id == "r2":
                raise RuntimeError("detail lookup failed")
            return super().genie_get_eval_result_details(space_id, eval_run_id, result_id)

    api = _OneBadDetail(
        _run("DONE", num_correct=1, num_done=2, num_questions=2),
        pages=[_list_page([_result("r1", "A?"), _result("r2", "B?")])],
        details={"r1": _details("GOOD"), "r2": _details("BAD")},
    )
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api), threshold=0.8)
    # only r1's question made it through; r2's lookup failed and was skipped, not guessed.
    assert d["n"] == 1 and d["questions"][0]["question"] == "A?"


def test_rest_gate_no_benchmarks_advisory_shape_has_no_questions_key():
    api = _FakeGenieEvalAPI(_run("DONE", num_correct=0, num_done=0, num_questions=0))
    d = eval_gate.run_eval_gate_rest("sp1", client=_client(api))
    assert d["status"] == "advisory" and "questions" not in d

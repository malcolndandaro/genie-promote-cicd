"""Unit tests for scripts/check_eval.py — the CI EVAL-01 (benchmark COUNT) merge gate. Pure/offline:
the count is read from the rendered serialized_space JSON (no WorkspaceClient), so this exercises
`main`'s exit codes directly (0 = pass/nothing-to-check, 1 = blocking count miss).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import check_eval  # noqa: E402


def _write(tmp_path, obj) -> str:
    p = tmp_path / "space.serialized_space.json"
    p.write_text(json.dumps(obj))
    return str(p)


def _run(monkeypatch, path, env=None):
    monkeypatch.setattr(sys, "argv", ["check_eval.py", path])
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return check_eval.main()


def test_fewer_than_min_benchmarks_blocks(monkeypatch, tmp_path):
    path = _write(tmp_path, {"version": 1, "benchmarks": {"questions": [{"id": "a", "question": "q1"}]}})
    assert _run(monkeypatch, path) == 1  # 1 < default 2 -> BLOCKER


def test_at_least_min_benchmarks_passes(monkeypatch, tmp_path):
    path = _write(tmp_path, {"version": 1, "benchmarks": {"questions": [
        {"id": "a", "question": "q1"}, {"id": "b", "question": "q2"}]}})
    assert _run(monkeypatch, path) == 0  # 2 >= default 2


def test_missing_benchmarks_key_counts_as_zero_and_blocks(monkeypatch, tmp_path):
    # The vendored-fixture shape (no `benchmarks` key at all) — must count 0 and block, exactly what
    # EVAL-01 exists to catch.
    path = _write(tmp_path, {"version": 1, "data_sources": {"tables": []}, "instructions": {}})
    assert _run(monkeypatch, path) == 1


def test_absent_file_is_a_noop_pass(monkeypatch, tmp_path):
    # render.sh produces nothing when there's no content overlay — the gate must be a clean no-op,
    # never a false failure (mirrors check_grants.py's file-absent early return 0).
    assert _run(monkeypatch, str(tmp_path / "does-not-exist.json")) == 0


def test_env_override_raises_the_floor(monkeypatch, tmp_path):
    path = _write(tmp_path, {"version": 1, "benchmarks": {"questions": [
        {"id": "a", "question": "q1"}, {"id": "b", "question": "q2"}]}})
    # 2 benchmarks passes the default (2) but not an override of 3.
    assert _run(monkeypatch, path, env={"EVAL_MIN_BENCHMARKS": "3"}) == 1
    assert _run(monkeypatch, path, env={"EVAL_MIN_BENCHMARKS": "2"}) == 0


def test_env_override_garbage_falls_back_to_default(monkeypatch, tmp_path):
    path = _write(tmp_path, {"version": 1, "benchmarks": {"questions": [{"id": "a", "question": "q1"}]}})
    # unparseable override -> default 2 -> 1 benchmark still blocks (never crashes on bad config).
    assert _run(monkeypatch, path, env={"EVAL_MIN_BENCHMARKS": "abc"}) == 1

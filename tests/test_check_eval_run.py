"""Unit tests for scripts/check_eval_run.py — the CI EVAL-RUN pass-rate merge gate. The eval-run
itself is stubbed (eval_gate.run_eval_gate_rest); these cover the dev-space-id recovery from the
slug and the block/advisory→exit-code mapping.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import check_eval_run  # noqa: E402


# --- dev space id recovery from the slug -------------------------------------------------------

def test_dev_id_from_default_slug_strips_the_s_prefix(monkeypatch):
    monkeypatch.delenv("APP_SPACE_SLUGS", raising=False)
    assert check_eval_run.dev_space_id_from_slug("s_01f1717fabc") == "01f1717fabc"


def test_dev_id_from_pinned_slug_inverts_the_map(monkeypatch):
    monkeypatch.setenv("APP_SPACE_SLUGS", json.dumps({"01f16e83dev": "receivables"}))
    assert check_eval_run.dev_space_id_from_slug("receivables") == "01f16e83dev"


def test_dev_id_unresolvable_slug_returns_none(monkeypatch):
    monkeypatch.setenv("APP_SPACE_SLUGS", "{}")
    assert check_eval_run.dev_space_id_from_slug("plain-name") is None


def test_slug_from_path():
    assert check_eval_run.slug_from_path("build/genie/receivables.serialized_space.json") == "receivables"


# --- main() exit codes -------------------------------------------------------------------------

def _run(monkeypatch, tmp_path, *, slug, eval_result, env=None):
    p = tmp_path / f"{slug}.serialized_space.json"
    p.write_text(json.dumps({"version": 1}))
    monkeypatch.setattr(sys, "argv", ["check_eval_run.py", str(p)])
    monkeypatch.setattr(check_eval_run, "WorkspaceClient", lambda *a, **k: NS())
    monkeypatch.setattr(check_eval_run.eval_gate, "run_eval_gate_rest",
                        lambda space_id, **kw: {**eval_result, "_space_id": space_id})
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    return check_eval_run.main()


def test_blocking_eval_run_fails_the_job(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_SPACE_SLUGS", raising=False)
    rc = _run(monkeypatch, tmp_path, slug="s_devid1",
              eval_result={"status": "block", "summary": "🔴 acertou 40% — abaixo de 80%"})
    assert rc == 1


def test_passing_eval_run_is_a_pass(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_SPACE_SLUGS", raising=False)
    rc = _run(monkeypatch, tmp_path, slug="s_devid1",
              eval_result={"status": "pass", "summary": "✅ 90%"})
    assert rc == 0


def test_advisory_eval_run_does_not_block(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_SPACE_SLUGS", raising=False)
    rc = _run(monkeypatch, tmp_path, slug="s_devid1",
              eval_result={"status": "advisory", "summary": "🟡 indisponível"})
    assert rc == 0


def test_unresolvable_slug_degrades_to_advisory_pass(monkeypatch, tmp_path):
    # A slug that's neither pinned nor s_-prefixed → can't resolve the dev id → advisory (never block),
    # and run_eval_gate_rest is never called.
    monkeypatch.setenv("APP_SPACE_SLUGS", "{}")
    called = {"n": 0}
    p = tmp_path / "plain.serialized_space.json"
    p.write_text(json.dumps({"version": 1}))
    monkeypatch.setattr(sys, "argv", ["check_eval_run.py", str(p)])
    monkeypatch.setattr(check_eval_run, "WorkspaceClient", lambda *a, **k: NS())
    monkeypatch.setattr(check_eval_run.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {"status": "block"})
    assert check_eval_run.main() == 0
    assert called["n"] == 0  # never attempted the eval-run


def test_absent_file_is_a_noop_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["check_eval_run.py", str(tmp_path / "nope.serialized_space.json")])
    assert check_eval_run.main() == 0


def test_env_threshold_override_is_passed_through(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_SPACE_SLUGS", raising=False)
    captured = {}
    p = tmp_path / "s_devid1.serialized_space.json"
    p.write_text(json.dumps({"version": 1}))
    monkeypatch.setattr(sys, "argv", ["check_eval_run.py", str(p)])
    monkeypatch.setattr(check_eval_run, "WorkspaceClient", lambda *a, **k: NS())

    def fake_run(space_id, *, threshold, **kw):
        captured["threshold"] = threshold
        captured["space_id"] = space_id
        return {"status": "pass", "summary": "ok"}

    monkeypatch.setattr(check_eval_run.eval_gate, "run_eval_gate_rest", fake_run)
    monkeypatch.setenv("EVAL_RUN_THRESHOLD", "0.6")
    check_eval_run.main()
    assert captured["threshold"] == 0.6
    assert captured["space_id"] == "devid1"  # s_devid1 -> devid1

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pilot_readiness  # noqa: E402


def test_active_scanner_reports_a_retired_literal_without_self_exemptions(tmp_path):
    source = tmp_path / "app"
    source.mkdir()
    literal = pilot_readiness.RETIRED_LITERALS[1]
    (source / "bad.py").write_text(f"value = {literal!r}\n", encoding="utf-8")
    findings = pilot_readiness.scan_retired_literals(tmp_path, ("app",))
    assert findings == [f"app/bad.py:1: {literal}"]
    (source / "bad.py").write_text("value = 'AudienceSpecIn'\n", encoding="utf-8")
    # The wire model's explicit canonical name is allowed; only the exact retired symbol is banned.
    assert pilot_readiness.scan_retired_literals(tmp_path, ("app",)) == []


def test_schema_and_scenario_contracts_are_machine_checkable():
    assert pilot_readiness.check_schema_contract() == []
    assert pilot_readiness.check_scenario_tests_exist(pilot_readiness.ROOT) == []


def test_lock_check_is_provider_neutral_and_requires_exact_engine_sha(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    content = tmp_path / "content"
    engine.mkdir()
    content.mkdir()
    sha = "a" * 40
    (content / "engine.lock").write_text(sha + "\n", encoding="utf-8")

    def fake_git(root: Path, *args: str) -> str:
        return sha if root == engine else "b" * 40

    monkeypatch.setattr(pilot_readiness, "_git", fake_git)
    ok, revisions = pilot_readiness.check_lock(engine, content)
    assert ok is True
    assert revisions["content_engine_lock"] == sha
    (content / "engine.lock").write_text("short\n", encoding="utf-8")
    assert pilot_readiness.check_lock(engine, content)[0] is False


def test_missing_live_evidence_is_explicit_pending_and_contains_no_secrets():
    redacted, checks = pilot_readiness.load_live_evidence(None)
    assert set(redacted) == set(pilot_readiness.LIVE_REQUIREMENTS)
    assert {check.status for check in checks} == {"PENDING"}
    assert json.dumps(redacted).casefold().find("private_key") == -1


def test_live_evidence_strips_url_query_and_rejects_secret_like_keys(tmp_path):
    valid = {
        key: {"status": "pass", "url": f"https://example.test/{key}?temporary=1", "note": "ok"}
        for key in pilot_readiness.LIVE_REQUIREMENTS
    }
    path = tmp_path / "live.json"
    path.write_text(json.dumps(valid), encoding="utf-8")
    redacted, checks = pilot_readiness.load_live_evidence(path)
    assert all(check.status == "PASS" for check in checks)
    assert all("?" not in entry["url"] for entry in redacted.values())

    valid["github_app"]["private_" + "key"] = "must never be accepted"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(ValueError, match="secret-like"):
        pilot_readiness.load_live_evidence(path)

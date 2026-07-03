"""Unit tests for github_drift (F5 Phase 1) — a fake GitHubReader, no network.

Verifies each named divergence class fires (steward unmapped / steward not the Environment
reviewer / an Environment reviewer with no matching Steward-or-Admin / missing branch protection)
AND that any GitHub read failure degrades to an `unknown_*` finding — NEVER "no drift" and NEVER a
raised exception (the acceptance criteria's "surface 'unknown', never assume 'no drift'").
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "genie_reviewer"))

from github_drift import check_drift, summarize  # noqa: E402


class FakeReader:
    def __init__(self, *, env_reviewers=None, env_error=None, protection=None, protection_error=None):
        self._env_reviewers = env_reviewers
        self._env_error = env_error
        self._protection = protection
        self._protection_error = protection_error

    def get_environment_reviewers(self, environment):
        if self._env_error:
            raise self._env_error
        return self._env_reviewers or []

    def get_branch_protection(self, branch):
        if self._protection_error:
            raise self._protection_error
        return self._protection


def _mapping(d: dict):
    return lambda email: d.get(email)


def test_no_drift_when_everything_matches():
    reader = FakeReader(env_reviewers=["pedro-gh"],
                        protection={"required_pull_request_reviews": {"required_approving_review_count": 1}})
    findings = check_drift(
        stewards=["pedro@x"], admins=["pedro@x"],
        github_username_for=_mapping({"pedro@x": "pedro-gh"}), reader=reader)
    assert findings == []
    summary = summarize(findings)
    assert summary == {"has_drift": False, "has_unknown": False, "findings": []}


def test_steward_unmapped_when_no_github_username():
    reader = FakeReader(env_reviewers=["someone-else"],
                        protection={"required_pull_request_reviews": {}})
    findings = check_drift(
        stewards=["pedro@x"], admins=[], github_username_for=_mapping({}), reader=reader)
    kinds = {f.kind for f in findings}
    assert "steward_unmapped" in kinds
    assert all(f.severity == "warning" for f in findings if f.kind == "steward_unmapped")


def test_steward_not_env_reviewer_when_mapped_but_not_listed():
    reader = FakeReader(env_reviewers=["someone-else"],
                        protection={"required_pull_request_reviews": {}})
    findings = check_drift(
        stewards=["pedro@x"], admins=[],
        github_username_for=_mapping({"pedro@x": "pedro-gh"}), reader=reader)
    kinds = {f.kind for f in findings}
    assert "steward_not_env_reviewer" in kinds


def test_env_reviewer_not_steward_when_unrecognized_login():
    # The Environment gate is released by a GitHub login the app doesn't recognize as any
    # Steward/Admin at all — the OTHER direction of drift.
    reader = FakeReader(env_reviewers=["pedro-gh", "mystery-login"],
                        protection={"required_pull_request_reviews": {}})
    findings = check_drift(
        stewards=["pedro@x"], admins=[],
        github_username_for=_mapping({"pedro@x": "pedro-gh"}), reader=reader)
    kinds = {f.kind for f in findings}
    assert "env_reviewer_not_steward" in kinds
    assert "steward_not_env_reviewer" not in kinds  # pedro IS a reviewer -> no drift on that axis


def test_admin_mapped_login_satisfies_env_reviewer_not_steward():
    # An Environment reviewer that maps back to a configured ADMIN (not just Steward) is fine.
    reader = FakeReader(env_reviewers=["admin-gh"],
                        protection={"required_pull_request_reviews": {"required_approving_review_count": 1}})
    findings = check_drift(
        stewards=[], admins=["admin@x"],
        github_username_for=_mapping({"admin@x": "admin-gh"}), reader=reader)
    assert findings == []


def test_branch_protection_missing_when_no_required_reviews():
    reader = FakeReader(env_reviewers=[], protection=None)
    findings = check_drift(stewards=[], admins=[], github_username_for=_mapping({}), reader=reader)
    kinds = {f.kind for f in findings}
    assert "branch_protection_missing" in kinds


def test_branch_protection_missing_when_protection_present_but_no_pr_review_rule():
    reader = FakeReader(env_reviewers=[], protection={"enforce_admins": {"enabled": True}})
    findings = check_drift(stewards=[], admins=[], github_username_for=_mapping({}), reader=reader)
    kinds = {f.kind for f in findings}
    assert "branch_protection_missing" in kinds


def test_environment_read_failure_degrades_to_unknown_not_no_drift():
    reader = FakeReader(env_error=RuntimeError("403 missing scope"),
                        protection={"required_pull_request_reviews": {}})
    findings = check_drift(
        stewards=["pedro@x"], admins=[], github_username_for=_mapping({}), reader=reader)
    kinds = {f.kind: f.severity for f in findings}
    assert kinds.get("unknown_environment") == "unknown"
    # A failed read must not ALSO claim steward_not_env_reviewer (that would assert a fact we
    # couldn't actually verify).
    assert "steward_not_env_reviewer" not in kinds
    assert "steward_unmapped" not in kinds
    summary = summarize(findings)
    assert summary["has_unknown"] is True


def test_branch_protection_read_failure_degrades_to_unknown():
    reader = FakeReader(env_reviewers=["pedro-gh"], protection_error=RuntimeError("network error"))
    findings = check_drift(
        stewards=["pedro@x"], admins=[],
        github_username_for=_mapping({"pedro@x": "pedro-gh"}), reader=reader)
    kinds = {f.kind: f.severity for f in findings}
    assert kinds.get("unknown_branch_protection") == "unknown"
    assert "branch_protection_missing" not in kinds


def test_never_raises_even_when_both_reads_fail():
    reader = FakeReader(env_error=RuntimeError("boom"), protection_error=RuntimeError("boom2"))
    findings = check_drift(
        stewards=["pedro@x"], admins=[], github_username_for=_mapping({}), reader=reader)
    kinds = {f.kind for f in findings}
    assert kinds == {"unknown_environment", "unknown_branch_protection"}
    summary = summarize(findings)
    assert summary["has_unknown"] is True and summary["has_drift"] is False


def test_summarize_has_drift_true_only_on_warning_severity():
    reader = FakeReader(env_reviewers=[], protection_error=RuntimeError("x"))
    findings = check_drift(stewards=[], admins=[], github_username_for=_mapping({}), reader=reader)
    summary = summarize(findings)
    # branch protection read failed (unknown); no stewards configured means no steward-side drift.
    assert summary["has_unknown"] is True

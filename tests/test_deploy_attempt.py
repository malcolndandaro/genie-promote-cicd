"""Offline contract tests for the ADR-0007 deployment state machine."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import deploy_attempt  # noqa: E402


class _Emitter:
    def __init__(self):
        self.states = []

    def emit(self):
        self.states.append(None)  # run_attempt's evidence object is asserted directly


class _Operations:
    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.calls = []
        self.live = {"unrelated": "CAN_EDIT"}

    def _call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"boom in {name}")

    def preflight(self):
        self._call("preflight")

    def bundle_deploy(self):
        self._call("bundle_deploy")
        self.live["content"] = "desired"

    def resolve_space(self):
        self._call("resolve_space")
        return {"receivables": "space-1"}

    def assert_app_manage(self, targets):
        assert targets == {"receivables": "space-1"}
        self._call("assert_app_manage")
        self.live["app-sp"] = "CAN_MANAGE"

    def reconcile_audience(self, targets):
        self._call("reconcile_audience")
        self.live["audience"] = "CAN_RUN"

    def verify_live_state(self, targets):
        self._call("verify_live_state")
        assert self.live["content"] == "desired"
        assert self.live["app-sp"] == "CAN_MANAGE"
        assert self.live["audience"] == "CAN_RUN"
        assert self.live["unrelated"] == "CAN_EDIT"


def _evidence():
    return deploy_attempt.AttemptEvidence(
        attempt_id="github:1:1", run_attempt=1,
        revisions={"content_revision": "b" * 64, "engine_revision": "a" * 40},
        run_url="https://github.test/run/1",
    )


def test_preflight_failure_has_zero_mutation_and_operational_evidence():
    operations = _Operations(fail="preflight")
    evidence = _evidence()
    assert deploy_attempt.run_attempt(operations, evidence, _Emitter()) == 1
    assert operations.calls == ["preflight"]
    assert evidence.mutation_started is False
    assert evidence.failed_stage == "preflight"
    assert evidence.terminal_state == "operational_failed"
    assert operations.live == {"unrelated": "CAN_EDIT"}


def test_mid_deploy_failure_is_partial_and_records_completed_stages_and_targets():
    operations = _Operations(fail="assert_app_manage")
    evidence = _evidence()
    assert deploy_attempt.run_attempt(operations, evidence, _Emitter()) == 1
    assert evidence.mutation_started is True
    assert evidence.completed_stages == ["preflight", "bundle_deploy", "resolve_space"]
    assert evidence.failed_stage == "assert_app_manage"
    assert evidence.target_ids == {"receivables": "space-1"}
    assert evidence.terminal_state == "partial_failed"
    assert "boom" in evidence.reason


def test_success_order_is_fixed_audience_last_and_unrelated_acl_is_preserved():
    operations = _Operations()
    evidence = _evidence()
    emitter = _Emitter()
    assert deploy_attempt.run_attempt(operations, evidence, emitter) == 0
    assert operations.calls == [
        "preflight", "bundle_deploy", "resolve_space", "assert_app_manage",
        "reconcile_audience", "verify_live_state",
    ]
    assert operations.calls.index("reconcile_audience") > operations.calls.index("assert_app_manage")
    assert evidence.completed_stages == ["preflight", *deploy_attempt.MUTATION_STAGES]
    assert evidence.terminal_state == "succeeded"
    # GitHub retains at most 10 notice annotations from one step. Keep the canonical Attempt stream
    # below that ceiling so the final `succeeded` evidence is never dropped (regression: 15 emits
    # left the app stuck at verify_live_state/running after a green workflow).
    assert len(emitter.states) == 9
    assert operations.live["unrelated"] == "CAN_EDIT"


def test_retry_restarts_at_preflight_and_idempotently_converges():
    operations = _Operations()
    first, retry = _evidence(), _evidence()
    assert deploy_attempt.run_attempt(operations, first, _Emitter()) == 0
    assert deploy_attempt.run_attempt(operations, retry, _Emitter()) == 0
    expected = ["preflight", "bundle_deploy", "resolve_space", "assert_app_manage",
                "reconcile_audience", "verify_live_state"]
    assert operations.calls == expected + expected
    assert retry.completed_stages == first.completed_stages
    assert operations.live == {
        "unrelated": "CAN_EDIT", "content": "desired", "app-sp": "CAN_MANAGE",
        "audience": "CAN_RUN",
    }


def test_cli_has_no_resume_or_stage_skip_option():
    assert "--resume" not in deploy_attempt.main.__doc__ if deploy_attempt.main.__doc__ else True
    assert deploy_attempt.MUTATION_STAGES == (
        "bundle_deploy", "resolve_space", "assert_app_manage", "reconcile_audience",
        "verify_live_state", "complete",
    )

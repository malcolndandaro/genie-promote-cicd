"""Offline contract tests for the ADR-0007 deployment state machine."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

from databricks.sdk.errors import NotFound, PermissionDenied
from databricks.sdk.service.tags import TagAssignment, TagPolicy, Value
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import deploy_attempt  # noqa: E402
import workflow_support  # noqa: E402


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

    def certify_space(self, targets):
        self._call("certify_space")
        assert self.live["content"] == "desired"
        assert self.live["app-sp"] == "CAN_MANAGE"
        assert self.live["audience"] == "CAN_RUN"
        self.live["certification"] = "certified"


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


def test_success_order_certifies_only_after_live_state_and_preserves_unrelated_acl():
    operations = _Operations()
    evidence = _evidence()
    emitter = _Emitter()
    assert deploy_attempt.run_attempt(operations, evidence, emitter) == 0
    assert operations.calls == [
        "preflight", "bundle_deploy", "resolve_space", "assert_app_manage",
        "reconcile_audience", "verify_live_state", "certify_space",
    ]
    assert operations.calls.index("reconcile_audience") > operations.calls.index("assert_app_manage")
    assert operations.calls.index("certify_space") > operations.calls.index("verify_live_state")
    assert evidence.completed_stages == ["preflight", *deploy_attempt.MUTATION_STAGES]
    assert evidence.terminal_state == "succeeded"
    # GitHub retains at most 10 notice annotations from one step. Omitting the redundant running
    # emission after the no-op complete stage preserves the terminal `succeeded` evidence
    # (regression: 15 emits left the app stuck at verify_live_state/running after a green workflow).
    assert len(emitter.states) == 9
    assert operations.live["unrelated"] == "CAN_EDIT"
    assert operations.live["certification"] == "certified"


def test_retry_restarts_at_preflight_and_idempotently_converges():
    operations = _Operations()
    first, retry = _evidence(), _evidence()
    assert deploy_attempt.run_attempt(operations, first, _Emitter()) == 0
    assert deploy_attempt.run_attempt(operations, retry, _Emitter()) == 0
    expected = ["preflight", "bundle_deploy", "resolve_space", "assert_app_manage",
                "reconcile_audience", "verify_live_state", "certify_space"]
    assert operations.calls == expected + expected
    assert retry.completed_stages == first.completed_stages
    assert operations.live == {
        "unrelated": "CAN_EDIT", "content": "desired", "app-sp": "CAN_MANAGE",
        "audience": "CAN_RUN", "certification": "certified",
    }


def test_cli_has_no_resume_or_stage_skip_option():
    assert "--resume" not in deploy_attempt.main.__doc__ if deploy_attempt.main.__doc__ else True
    assert deploy_attempt.MUTATION_STAGES == (
        "bundle_deploy", "resolve_space", "assert_app_manage", "reconcile_audience",
        "verify_live_state", "certify_space", "complete",
    )


def test_certification_failure_is_partial_after_live_state_evidence():
    operations = _Operations(fail="certify_space")
    evidence = _evidence()

    assert deploy_attempt.run_attempt(operations, evidence, _Emitter()) == 1
    assert evidence.completed_stages == [
        "preflight", "bundle_deploy", "resolve_space", "assert_app_manage",
        "reconcile_audience", "verify_live_state",
    ]
    assert evidence.failed_stage == "certify_space"
    assert evidence.terminal_state == "partial_failed"


class _TagAssignments:
    def __init__(self, values: dict[tuple[str, str, str], str] | None = None):
        self.values = values or {}
        self.calls = []

    def get_tag_assignment(self, entity_type, entity_id, tag_key):
        self.calls.append(("get", entity_type, entity_id, tag_key))
        value = self.values.get((entity_type, entity_id, tag_key))
        if value is None:
            raise NotFound("tag assignment is absent")
        return TagAssignment(
            entity_type=entity_type,
            entity_id=entity_id,
            tag_key=tag_key,
            tag_value=value,
        )

    def create_tag_assignment(self, tag_assignment):
        self.calls.append(("create", tag_assignment))
        assert isinstance(tag_assignment, TagAssignment)
        self.values[(tag_assignment.entity_type, tag_assignment.entity_id, tag_assignment.tag_key)] = (
            tag_assignment.tag_value
        )
        return tag_assignment

    def update_tag_assignment(self, entity_type, entity_id, tag_key, tag_assignment, update_mask):
        self.calls.append(("update", entity_type, entity_id, tag_key, tag_assignment, update_mask))
        assert isinstance(tag_assignment, TagAssignment)
        self.values[(entity_type, entity_id, tag_key)] = tag_assignment.tag_value
        return tag_assignment


class _TagPolicies:
    def __init__(self, values, calls):
        self.values = values
        self.calls = calls

    def get_tag_policy(self, tag_key):
        self.calls.append(("get_tag_policy", tag_key))
        return TagPolicy(tag_key=tag_key, values=[Value(name=value) for value in self.values])


def _production_operations_for_certification_preflight(tag_values):
    calls = []
    policies = _TagPolicies(tag_values, calls)
    client = NS(
        current_user=NS(me=lambda: NS(id="ci-sp")),
        apps=NS(get=lambda _name: NS(service_principal_client_id="app-sp")),
        tag_policies=policies,
        genie=NS(list_spaces=lambda: NS(spaces=[])),
    )
    operations = deploy_attempt.ProductionOperations(
        deploy_attempt.ROOT,
        "warehouse-1",
        client=client,
    )
    operations._run = lambda *args: calls.append(("run", args))
    operations._artifacts = lambda: []
    return operations, calls


def test_preflight_requires_the_certified_value_in_the_governed_tag_policy():
    operations, calls = _production_operations_for_certification_preflight(["deprecated"])
    evidence = _evidence()

    assert deploy_attempt.run_attempt(operations, evidence, _Emitter()) == 1
    assert evidence.failed_stage == "preflight"
    assert evidence.terminal_state == "operational_failed"
    assert ("get_tag_policy", "system.certification_status") in calls
    assert not any(call == ("run", ("databricks", "bundle", "deploy", "-t", "prod", "--var",
                                     "warehouse_id=warehouse-1")) for call in calls)


def test_preflight_accepts_a_tag_policy_that_declares_certified():
    operations, calls = _production_operations_for_certification_preflight(
        ["deprecated", "certified"],
    )

    operations.preflight()

    assert ("get_tag_policy", "system.certification_status") in calls


def _production_operations_with_tags(tags):
    return deploy_attempt.ProductionOperations(
        deploy_attempt.ROOT,
        "warehouse-1",
        client=NS(workspace_entity_tag_assignments=tags),
    )


def test_certify_space_recreates_a_manually_deleted_tag_then_reads_back_certified():
    tags = _TagAssignments()

    _production_operations_with_tags(tags).certify_space({"receivables": "space-1"})

    assert tags.calls[0] == (
        "get", "geniespaces", "space-1", "system.certification_status",
    )
    created = tags.calls[1][1]
    assert (created.entity_type, created.entity_id, created.tag_key, created.tag_value) == (
        "geniespaces", "space-1", "system.certification_status", "certified",
    )
    assert tags.calls[2] == (
        "get", "geniespaces", "space-1", "system.certification_status",
    )


def test_certify_space_replaces_a_deprecated_tag_then_reads_back_certified():
    tags = _TagAssignments({("geniespaces", "space-1", "system.certification_status"): "deprecated"})

    _production_operations_with_tags(tags).certify_space({"receivables": "space-1"})

    assert tags.calls[0] == (
        "get", "geniespaces", "space-1", "system.certification_status",
    )
    _, entity_type, entity_id, tag_key, updated, update_mask = tags.calls[1]
    assert (entity_type, entity_id, tag_key, update_mask) == (
        "geniespaces", "space-1", "system.certification_status", "tag_value",
    )
    assert (updated.entity_type, updated.entity_id, updated.tag_key, updated.tag_value) == (
        "geniespaces", "space-1", "system.certification_status", "certified",
    )
    assert tags.calls[2] == (
        "get", "geniespaces", "space-1", "system.certification_status",
    )


def test_certify_space_is_a_noop_for_an_already_certified_space_but_reads_back():
    tags = _TagAssignments({("geniespaces", "space-1", "system.certification_status"): "certified"})

    _production_operations_with_tags(tags).certify_space({"receivables": "space-1"})

    assert tags.calls == [
        ("get", "geniespaces", "space-1", "system.certification_status"),
        ("get", "geniespaces", "space-1", "system.certification_status"),
    ]


def test_certify_space_propagates_a_non_not_found_sdk_failure():
    class _DeniedTagAssignments:
        def get_tag_assignment(self, *_args):
            raise PermissionDenied("CI service principal cannot manage tags")

    with pytest.raises(PermissionDenied, match="cannot manage tags"):
        _production_operations_with_tags(_DeniedTagAssignments()).certify_space(
            {"receivables": "space-1"},
        )


def test_certification_readback_mismatch_is_a_partial_failure():
    class _TagAssignmentsWithStaleReadback(_TagAssignments):
        def update_tag_assignment(self, entity_type, entity_id, tag_key, tag_assignment, update_mask):
            self.calls.append(("update", entity_type, entity_id, tag_key, tag_assignment, update_mask))
            return tag_assignment

    tags = _TagAssignmentsWithStaleReadback({
        ("geniespaces", "space-1", "system.certification_status"): "deprecated",
    })
    production = _production_operations_with_tags(tags)

    class _OperationsWithCertificationReadbackFailure(_Operations):
        def certify_space(self, targets):
            self._call("certify_space")
            production.certify_space(targets)

    evidence = _evidence()
    assert deploy_attempt.run_attempt(
        _OperationsWithCertificationReadbackFailure(), evidence, _Emitter(),
    ) == 1
    assert evidence.failed_stage == "certify_space"
    assert evidence.terminal_state == "partial_failed"
    assert "RuntimeError: receivables: certification tag readback was not certified" in evidence.reason
    assert [call[0] for call in tags.calls] == ["get", "update", "get"]


def test_post_deploy_resolver_retries_a_stale_genie_listing_until_the_title_appears():
    """A successful bundle deploy can briefly precede list-spaces consistency."""
    title = "Laboratório E2E — Governança de Recebíveis — 8fd8cc5d"

    class _Genie:
        def __init__(self):
            self.responses = [
                [NS(space_id="existing", title="Other Space")],
                [NS(space_id="new-space", title=title)],
            ]

        def list_spaces(self):
            return NS(spaces=self.responses.pop(0))

    waits = []
    workspace = NS(genie=_Genie())

    assert workflow_support.resolve_space_id(
        workspace, title, max_attempts=2, sleep=waits.append,
    ) == "new-space"
    assert waits == [2]

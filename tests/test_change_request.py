import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import change_request  # noqa: E402


ENGINE = "a" * 40


def _observation(revisions):
    return change_request.ChangeRequestObservation(
        provider="github",
        external_id="42",
        external_url="https://github.example/pr/42",
        state="closed",
        merged=True,
        phase="deployed",
        checks="success",
        check_observations=(),
        review_decision="approved",
        actors={"merged_by": "pedro", "deploy_approver": "pedro"},
        revisions=revisions,
        deployment=change_request.DeploymentObservation(
            status="completed",
            conclusion="success",
            waiting_approval=False,
            run_url="https://github.example/run/9",
            run_id=9,
            approver="pedro",
            revisions=revisions,
        ),
    )


def test_content_revision_is_stable_and_engine_lock_requires_immutable_sha():
    first = change_request.compute_content_revision({"b": "2", "a": "1"}, ["z", "z"])
    second = change_request.compute_content_revision({"a": "1", "b": "2"}, ["z"])
    assert first == second and len(first) == 64
    assert change_request.parse_engine_lock(f"{ENGINE}\n") == ENGINE
    with pytest.raises(ValueError, match="full lowercase"):
        change_request.parse_engine_lock("main")


def test_canonical_observation_keeps_provider_payloads_out_and_legacy_aliases_temporary():
    pair = change_request.RevisionPair("b" * 64, ENGINE)
    wire = _observation(pair).to_dict()
    assert wire["provider"] == "github"
    assert wire["external_id"] == "42"
    assert wire["deployment"]["revisions"] == pair.to_dict()
    assert wire["deploy"] == wire["deployment"]
    assert "workflow_runs" not in wire and "pull_request" not in wire


def test_revision_mismatch_rejects_deployment_observation():
    approved = change_request.RevisionPair("b" * 64, ENGINE)
    observed = change_request.RevisionPair("c" * 64, ENGINE)
    rejected = change_request.reject_mismatched_deployment(_observation(observed), approved)
    assert rejected.phase == "revision_mismatch"
    assert rejected.deployment.rejected is True
    assert rejected.deployment.conclusion == "failure"
    assert "approved" in rejected.deployment.rejection_reason

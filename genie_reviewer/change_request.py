"""Provider-neutral Change Request observations and immutable revision pairs.

GitHub remains the pilot adapter. This module owns the domain vocabulary it returns so the
Promotion store, audit reconciler and UI never need to understand raw GitHub payloads.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Mapping, Sequence


_ENGINE_SHA = re.compile(r"^[0-9a-f]{40}$")
_CONTENT_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclasses.dataclass(frozen=True)
class RevisionPair:
    """The exact content declaration and engine commit approved for one Change Request."""

    content_revision: str
    engine_revision: str

    def __post_init__(self) -> None:
        if not _CONTENT_SHA.fullmatch(self.content_revision):
            raise ValueError("content_revision must be a lowercase sha256")
        if not _ENGINE_SHA.fullmatch(self.engine_revision):
            raise ValueError("engine_revision must be a full lowercase git commit SHA")

    def to_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(value: Mapping | None) -> "RevisionPair | None":
        if not value:
            return None
        return RevisionPair(
            content_revision=str(value.get("content_revision") or ""),
            engine_revision=str(value.get("engine_revision") or ""),
        )


def parse_engine_lock(raw: str | None) -> str:
    """Validate the content repository's single-line immutable engine lock."""
    value = (raw or "").strip()
    if not _ENGINE_SHA.fullmatch(value):
        raise ValueError("engine.lock must contain exactly one full lowercase 40-character commit SHA")
    return value


def compute_content_revision(files: Mapping[str, str], removed_files: Sequence[str] = ()) -> str:
    """Stable digest of the desired promotion tree, excluding its self-describing manifest."""
    canonical = {
        "files": [{"path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                  for path, content in sorted(files.items())],
        "removed_files": sorted(set(removed_files)),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclasses.dataclass(frozen=True)
class CheckObservation:
    name: str
    conclusion: str | None
    summary: str
    details_url: str | None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class DeploymentObservation:
    status: str
    conclusion: str | None
    waiting_approval: bool
    run_url: str | None
    run_id: int | None
    approver: str | None
    revisions: RevisionPair | None = None
    attempt: Mapping | None = None
    rejected: bool = False
    rejection_reason: str | None = None

    def to_dict(self) -> dict:
        out = dataclasses.asdict(self)
        out["revisions"] = self.revisions.to_dict() if self.revisions else None
        return out


@dataclasses.dataclass(frozen=True)
class ChangeRequestObservation:
    provider: str
    external_id: str
    external_url: str | None
    state: str | None
    merged: bool
    phase: str
    checks: str
    check_observations: tuple[CheckObservation, ...]
    review_decision: str
    actors: Mapping[str, str | None]
    revisions: RevisionPair | None
    deployment: DeploymentObservation

    def to_dict(self) -> dict:
        """Canonical fields plus temporary legacy aliases consumed by the current SPA."""
        checks_detail = [item.to_dict() for item in self.check_observations] or None
        deployment = self.deployment.to_dict()
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "external_url": self.external_url,
            "state": self.state,
            "merged": self.merged,
            "phase": self.phase,
            "checks": self.checks,
            "check_observations": checks_detail,
            "review_decision": self.review_decision,
            "actors": dict(self.actors),
            "revisions": self.revisions.to_dict() if self.revisions else None,
            "deployment": deployment,
            # Phase-1 compatibility aliases. No raw provider payload is exposed.
            "pr_state": self.state,
            "pr_url": self.external_url,
            "checks_detail": checks_detail,
            "deploy": deployment,
        }


def reject_mismatched_deployment(
    observation: ChangeRequestObservation,
    approved: RevisionPair | None,
) -> ChangeRequestObservation:
    """Reject, rather than report, a deployment that ran a different approved pair."""
    deployed = observation.deployment
    if (
        approved is None
        or deployed.status in {"none", "waiting"}
        or deployed.revisions is None
        or deployed.revisions == approved
    ):
        return observation
    reason = (
        "deployment revision mismatch: approved "
        f"{approved.content_revision[:12]}/{approved.engine_revision[:12]}, observed "
        f"{deployed.revisions.content_revision[:12]}/{deployed.revisions.engine_revision[:12]}"
    )
    rejected = dataclasses.replace(
        deployed, rejected=True, rejection_reason=reason, conclusion="failure"
    )
    return dataclasses.replace(observation, phase="revision_mismatch", deployment=rejected)

#!/usr/bin/env python3
"""Safe, observable and replayable production deployment (ADR-0007).

The workflow invokes this file once, immediately after the Steward gate.  There is deliberately no
``--resume`` argument: every invocation repeats the mutation-free preflight and then replays the
same idempotent stages in the same order.  GitHub annotations are the live evidence source; the app
reconciles their canonical ``DEPLOY_ATTEMPT:`` payload into Lakebase.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "genie_reviewer"))
sys.path.insert(0, str(ROOT / "scripts"))

import audience_spec  # noqa: E402
import change_request  # noqa: E402
import content_revision  # noqa: E402
import reconcile_audience  # noqa: E402
from workflow_support import resolve_space_id  # noqa: E402


PREFLIGHT = "preflight"
MUTATION_STAGES = (
    "bundle_deploy",
    "resolve_space",
    "assert_app_manage",
    "reconcile_audience",
    "verify_live_state",
    "certify_space",
    "complete",
)
_SUFFICIENT = {"CAN_RUN", "CAN_EDIT", "CAN_MANAGE", "IS_OWNER"}
_TECHNICAL_SUFFICIENT = {"CAN_MANAGE", "IS_OWNER"}
_EVIDENCE_PREFIX = "DEPLOY_ATTEMPT:"
_GENIE_SPACE_TAG_ENTITY_TYPE = "geniespaces"
_CERTIFICATION_TAG_KEY = "system.certification_status"
_CERTIFICATION_TAG_VALUE = "certified"


def _gh_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _safe_reason(exc: BaseException) -> str:
    value = f"{type(exc).__name__}: {exc}"
    for name in ("DATABRICKS_CLIENT_SECRET", "GITHUB_TOKEN", "GH_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value[:1000]


@dataclasses.dataclass
class AttemptEvidence:
    attempt_id: str
    run_attempt: int
    revisions: dict[str, str]
    run_url: str | None
    mutation_started: bool = False
    completed_stages: list[str] = dataclasses.field(default_factory=list)
    current_stage: str = PREFLIGHT
    failed_stage: str | None = None
    target_ids: dict[str, str] = dataclasses.field(default_factory=dict)
    reason: str | None = None
    terminal_state: str = "running"
    sequence: int = 0
    version: int = 1

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class Operations(Protocol):
    def preflight(self) -> None: ...
    def bundle_deploy(self) -> None: ...
    def resolve_space(self) -> dict[str, str]: ...
    def assert_app_manage(self, targets: dict[str, str]) -> None: ...
    def reconcile_audience(self, targets: dict[str, str]) -> None: ...
    def verify_live_state(self, targets: dict[str, str]) -> None: ...
    def certify_space(self, targets: dict[str, str]) -> None: ...


class EvidenceEmitter:
    def __init__(self, evidence: AttemptEvidence):
        self.evidence = evidence

    def emit(self) -> None:
        self.evidence.sequence += 1
        payload = json.dumps(self.evidence.to_dict(), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        print(f"::notice title=deploy-attempt::{_gh_escape(_EVIDENCE_PREFIX + payload)}")
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(f"terminal_state={self.evidence.terminal_state}\n")
                handle.write(f"attempt_id={self.evidence.attempt_id}\n")


def run_attempt(operations: Operations, evidence: AttemptEvidence,
                emitter: EvidenceEmitter | None = None) -> int:
    """Run the fixed state machine.  A failed mutation is reported honestly, never rolled back."""
    emitter = emitter or EvidenceEmitter(evidence)
    emitter.emit()
    try:
        operations.preflight()
        evidence.completed_stages.append(PREFLIGHT)
        evidence.current_stage = MUTATION_STAGES[0]
        emitter.emit()
    except BaseException as exc:  # noqa: BLE001 - preserve the exact workflow failure
        evidence.failed_stage = PREFLIGHT
        evidence.reason = _safe_reason(exc)
        evidence.terminal_state = "operational_failed"
        emitter.emit()
        print(f"::error title=deploy-preflight::{_gh_escape(evidence.reason)}")
        return 1

    actions = {
        "bundle_deploy": lambda: operations.bundle_deploy(),
        "resolve_space": lambda: evidence.target_ids.update(operations.resolve_space()),
        "assert_app_manage": lambda: operations.assert_app_manage(evidence.target_ids),
        "reconcile_audience": lambda: operations.reconcile_audience(evidence.target_ids),
        "verify_live_state": lambda: operations.verify_live_state(evidence.target_ids),
        "certify_space": lambda: operations.certify_space(evidence.target_ids),
        "complete": lambda: None,
    }
    for index, stage in enumerate(MUTATION_STAGES):
        evidence.current_stage = stage
        if stage == "bundle_deploy":
            # The CLI can mutate before returning an error, so mark this before invoking it.
            evidence.mutation_started = True
        try:
            actions[stage]()
        except BaseException as exc:  # noqa: BLE001 - convert to durable partial evidence
            evidence.failed_stage = stage
            evidence.reason = _safe_reason(exc)
            evidence.terminal_state = "partial_failed"
            emitter.emit()
            print(f"::error title=deploy-{stage}::{_gh_escape(evidence.reason)}")
            return 1
        evidence.completed_stages.append(stage)
        if index + 1 < len(MUTATION_STAGES):
            evidence.current_stage = MUTATION_STAGES[index + 1]
        if stage != "complete":
            emitter.emit()

    evidence.current_stage = "complete"
    evidence.terminal_state = "succeeded"
    emitter.emit()
    return 0


class ProductionOperations:
    """Thin production adapter; domain ordering stays in ``run_attempt`` and is fully fakeable."""

    def __init__(self, root: Path, warehouse_id: str, previous_content_root: Path | None = None,
                 client=None):
        self.root = root
        self.warehouse_id = warehouse_id
        self.previous_content_root = previous_content_root
        if client is None:
            from databricks.sdk import WorkspaceClient
            client = WorkspaceClient()
        self.w = client
        self.app_name = os.environ.get("APP_NAME", "genie-promote-app")

    def _run(self, *args: str) -> None:
        subprocess.run(list(args), cwd=self.root, check=True)

    def _artifacts(self) -> list[tuple[str, Path, Path, Path]]:
        out = []
        for rendered in sorted((self.root / "build" / "genie").glob("*.serialized_space.json")):
            slug = rendered.name.removesuffix(".serialized_space.json")
            title = rendered.with_name(f"{slug}.title")
            audience = rendered.with_name(f"{slug}.audience.json")
            if not title.exists() or not title.read_text(encoding="utf-8").strip():
                raise ValueError(f"{slug}: required non-empty title sidecar is missing")
            if not audience.exists():
                raise ValueError(f"{slug}: required AudienceSpec sidecar is missing")
            with audience.open(encoding="utf-8") as handle:
                audience_spec.parse_sidecar(json.load(handle))
            out.append((slug, rendered, title, audience))
        if not out:
            raise ValueError("no rendered Genie Space artifacts found")
        return out

    def preflight(self) -> None:
        # These commands write only the runner's local build directory; production is untouched.
        self._run("bash", "scripts/render.sh", "prod")
        self._run("bash", "scripts/build_promote_app.sh")
        self._run("databricks", "bundle", "validate", "--strict", "-t", "prod", "--var",
                  f"warehouse_id={self.warehouse_id}")
        me = self.w.current_user.me()
        if not getattr(me, "id", None):
            raise RuntimeError("deployment identity could not be resolved")
        app = self.w.apps.get(self.app_name)
        if not getattr(app, "service_principal_client_id", None):
            raise RuntimeError(f"app {self.app_name!r} has no service principal")
        certification_policy = self.w.tag_policies.get_tag_policy(_CERTIFICATION_TAG_KEY)
        declared_certification_values = {
            value.name for value in certification_policy.values or []
        }
        if _CERTIFICATION_TAG_VALUE not in declared_certification_values:
            raise RuntimeError(
                f"certification tag policy {_CERTIFICATION_TAG_KEY!r} does not declare "
                f"{_CERTIFICATION_TAG_VALUE!r}",
            )
        live = list(self.w.genie.list_spaces().spaces or [])
        for slug, rendered, title_path, audience_path in self._artifacts():
            title = title_path.read_text(encoding="utf-8").strip()
            matches = [space for space in live if (space.title or "") == title]
            if len(matches) > 1:
                raise ValueError(f"{slug}: duplicate live title {title!r}")
            if matches:
                # A read-only capability probe for the post-deploy ACL stages.
                self.w.permissions.get(request_object_type="genie",
                                       request_object_id=matches[0].space_id)
            self._run(sys.executable, "scripts/check_audience.py", str(rendered),
                      str(audience_path))

    def bundle_deploy(self) -> None:
        self._run("databricks", "bundle", "deploy", "-t", "prod", "--var",
                  f"warehouse_id={self.warehouse_id}")

    def resolve_space(self) -> dict[str, str]:
        return {
            slug: resolve_space_id(self.w, title.read_text(encoding="utf-8").strip())
            for slug, _rendered, title, _audience in self._artifacts()
        }

    def assert_app_manage(self, targets: dict[str, str]) -> None:
        from databricks.sdk.service import iam
        app_sp = self.w.apps.get(self.app_name).service_principal_client_id
        for space_id in targets.values():
            self.w.permissions.update(
                request_object_type="genie", request_object_id=space_id,
                access_control_list=[iam.AccessControlRequest(
                    service_principal_name=app_sp,
                    permission_level=iam.PermissionLevel.CAN_MANAGE,
                )],
            )

    def _previous(self, slug: str):
        if self.previous_content_root is None:
            return None
        base = self.previous_content_root / "src" / "genie"
        path = base / f"{slug}.audience.json"
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                return audience_spec.parse_sidecar(json.load(handle))
        return None

    def reconcile_audience(self, targets: dict[str, str]) -> None:
        for slug, _rendered, _title, audience_path in self._artifacts():
            with audience_path.open(encoding="utf-8") as handle:
                desired = audience_spec.parse_sidecar(json.load(handle))
            reconcile_audience.reconcile(
                self.w, targets[slug], desired, previous=self._previous(slug))

    def verify_live_state(self, targets: dict[str, str]) -> None:
        app_sp = self.w.apps.get(self.app_name).service_principal_client_id
        for slug, _rendered, _title, audience_path in self._artifacts():
            with audience_path.open(encoding="utf-8") as handle:
                desired = audience_spec.parse_sidecar(json.load(handle))
            acl = self.w.permissions.get(
                request_object_type="genie", request_object_id=targets[slug]).access_control_list or []
            by_name = {
                reconcile_audience._name(entry): reconcile_audience._direct_level(entry)
                for entry in acl
            }
            if by_name.get(app_sp) not in _TECHNICAL_SUFFICIENT:
                raise RuntimeError(f"{slug}: app service principal CAN_MANAGE readback failed")
            missing = [p.name for p in desired.principals if by_name.get(p.name) not in _SUFFICIENT]
            if missing:
                raise RuntimeError(f"{slug}: audience readback failed for {', '.join(missing)}")

    def certify_space(self, targets: dict[str, str]) -> None:
        """Assert and read back the required system certification tag for every deployed Space."""
        from databricks.sdk.errors import NotFound
        from databricks.sdk.service.tags import TagAssignment

        assignments = self.w.workspace_entity_tag_assignments
        for slug, space_id in targets.items():
            try:
                current = assignments.get_tag_assignment(
                    _GENIE_SPACE_TAG_ENTITY_TYPE, space_id, _CERTIFICATION_TAG_KEY,
                )
            except NotFound:
                assignments.create_tag_assignment(TagAssignment(
                    entity_type=_GENIE_SPACE_TAG_ENTITY_TYPE,
                    entity_id=space_id,
                    tag_key=_CERTIFICATION_TAG_KEY,
                    tag_value=_CERTIFICATION_TAG_VALUE,
                ))
            else:
                if current.tag_value != _CERTIFICATION_TAG_VALUE:
                    assignments.update_tag_assignment(
                        _GENIE_SPACE_TAG_ENTITY_TYPE,
                        space_id,
                        _CERTIFICATION_TAG_KEY,
                        TagAssignment(
                            entity_type=_GENIE_SPACE_TAG_ENTITY_TYPE,
                            entity_id=space_id,
                            tag_key=_CERTIFICATION_TAG_KEY,
                            tag_value=_CERTIFICATION_TAG_VALUE,
                        ),
                        update_mask="tag_value",
                    )

            readback = assignments.get_tag_assignment(
                _GENIE_SPACE_TAG_ENTITY_TYPE, space_id, _CERTIFICATION_TAG_KEY,
            )
            if readback.tag_value != _CERTIFICATION_TAG_VALUE:
                raise RuntimeError(f"{slug}: certification tag readback was not certified")


def _evidence(root: Path) -> AttemptEvidence:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    engine_revision = os.environ.get("ENGINE_REVISION", "0" * 40)
    # Validation is intentional: a deploy cannot proceed with a floating/short engine revision.
    change_request.parse_engine_lock(engine_revision)
    content_revision_value = (
        os.environ.get("CONTENT_REVISION")
        or content_revision.compute_content_tree_revision(root)
    )
    change_request.RevisionPair(content_revision_value, engine_revision)
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if server and repo else None
    return AttemptEvidence(
        attempt_id=f"github:{run_id}:{run_attempt}", run_attempt=run_attempt,
        revisions={"content_revision": content_revision_value, "engine_revision": engine_revision},
        run_url=run_url,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the fixed ADR-0007 production state machine")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--previous-content-root")
    args = parser.parse_args(argv)
    previous = Path(args.previous_content_root).resolve() if args.previous_content_root else None
    evidence = _evidence(ROOT)
    operations = ProductionOperations(ROOT, args.warehouse_id, previous)
    return run_attempt(operations, evidence)


if __name__ == "__main__":
    raise SystemExit(main())

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
import time
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "genie_reviewer"))
sys.path.insert(0, str(ROOT / "scripts"))

import audience_spec  # noqa: E402
import change_request  # noqa: E402
import content_revision  # noqa: E402
import reconcile_audience  # noqa: E402
import resource_kind  # noqa: E402  (the ONE registry of per-kind facts)
import workspace_resource  # noqa: E402  (the per-kind Databricks API adapter)


PREFLIGHT = "preflight"
# The stage NAMES are a persisted contract: they are stored in
# `deployment_attempts.completed_stages` and rendered in the app's deploy panel. Adding a second
# resource kind therefore does NOT add stages — each stage now iterates the artifacts of EVERY kind.
MUTATION_STAGES = (
    "bundle_deploy",
    "resolve_space",
    "assert_app_manage",
    "reconcile_audience",
    "verify_live_state",
    "certify_space",
    "complete",
)
# Levels that count as "this principal can use the resource" — PER KIND, deliberately not one shared
# set. `CAN_READ` IS assignable on object type `genie` (verified live), so a shared set would make a
# Genie readback ACCEPT a principal holding only CAN_READ — weaker than before dashboards existed.
# The readback must hold each kind to the level THAT kind's audience actually derives.
_GENIE_SUFFICIENT = {"CAN_RUN", "CAN_EDIT", "CAN_MANAGE", "IS_OWNER"}
_DASHBOARD_SUFFICIENT = _GENIE_SUFFICIENT | {"CAN_READ"}
_SUFFICIENT_BY_KIND = {
    resource_kind.GENIE_SPACE: _GENIE_SUFFICIENT,
    resource_kind.DASHBOARD: _DASHBOARD_SUFFICIENT,
}
# Back-compat alias, pinned to the NARROWEST set so a stale reference fails closed.
_SUFFICIENT = _GENIE_SUFFICIENT
_TECHNICAL_SUFFICIENT = {"CAN_MANAGE", "IS_OWNER"}


def _sufficient_for(kind) -> set:
    """The levels that satisfy an audience readback for one kind (narrowest set on an unknown kind)."""
    return _SUFFICIENT_BY_KIND.get(getattr(kind, "kind", kind), _GENIE_SUFFICIENT)
_EVIDENCE_PREFIX = "DEPLOY_ATTEMPT:"
_CERTIFICATION_TAG_KEY = "system.certification_status"
_CERTIFICATION_TAG_VALUE = "certified"
_CERTIFICATION_READBACK_MAX_ATTEMPTS = 30
_CERTIFICATION_READBACK_RETRY_SECONDS = 2.0


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
                 client=None, *,
                 certification_readback_max_attempts: int = _CERTIFICATION_READBACK_MAX_ATTEMPTS,
                 certification_readback_retry_seconds: float = _CERTIFICATION_READBACK_RETRY_SECONDS,
                 certification_readback_sleep: Callable[[float], None] = time.sleep):
        if certification_readback_max_attempts < 1:
            raise ValueError("certification_readback_max_attempts must be at least 1")
        self.root = root
        self.warehouse_id = warehouse_id
        self.previous_content_root = previous_content_root
        self.certification_readback_max_attempts = certification_readback_max_attempts
        self.certification_readback_retry_seconds = certification_readback_retry_seconds
        self.certification_readback_sleep = certification_readback_sleep
        if client is None:
            from databricks.sdk import WorkspaceClient
            client = WorkspaceClient()
        self.w = client
        self.app_name = os.environ.get("APP_NAME", "genie-promote-app")

    def _run(self, *args: str) -> None:
        subprocess.run(list(args), cwd=self.root, check=True)

    def _artifacts_of(self, kind) -> list[tuple[str, Path, Path, Path]]:
        """The rendered artifacts of ONE kind: ``(slug, rendered, title_path, audience_path)``.

        The title + AudienceSpec sidecars are REQUIRED for every kind: the title is the deploy's only
        id-resolution key (neither `bundle summary` nor the DABs deploy reports a Genie space id or a
        dashboard id back), and the AudienceSpec is the reconciled desired set. A missing one fails
        here rather than mid-mutation.
        """
        out = []
        build_dir = self.root / "build" / kind.build_subdir
        if kind.nested_layout:
            # NESTED: build/dashboards/<area>/<name>/dashboard.lvdash.json, sidecars beside it with
            # fixed names. The slug is the path between the build dir and the artifact.
            # `as_posix()` so the slug uses `/` on every platform — it is a git path and a
            # `target_ids` key, never an OS-local path.
            found = [(p.parent.relative_to(build_dir).as_posix(), p)
                     for p in sorted(build_dir.glob(f"**/dashboard{kind.artifact_suffix}"))]
        else:
            # FLAT: build/genie/<slug>.serialized_space.json with `<slug>.`-prefixed sidecars.
            found = [(p.name.removesuffix(kind.artifact_suffix), p)
                     for p in sorted(build_dir.glob(f"*{kind.artifact_suffix}"))]
        for slug, rendered in found:
            if kind.nested_layout:
                title = rendered.with_name("title")
                audience = rendered.with_name("audience.json")
            else:
                title = rendered.with_name(f"{slug}.title")
                audience = rendered.with_name(f"{slug}.audience.json")
            if not title.exists() or not title.read_text(encoding="utf-8").strip():
                raise ValueError(f"{slug}: required non-empty title sidecar is missing")
            if not audience.exists():
                raise ValueError(f"{slug}: required AudienceSpec sidecar is missing")
            with audience.open(encoding="utf-8") as handle:
                audience_spec.parse_sidecar(json.load(handle))
            out.append((slug, rendered, title, audience))
        return out

    def _all_artifacts(self) -> list[tuple[object, str, Path, Path, Path]]:
        """Every rendered artifact across EVERY kind, each tagged with its kind.

        The "nothing to deploy" refusal is evaluated over the UNION, not per kind: a promotion that
        contains only dashboards (or only Spaces) is perfectly valid, while a genuinely empty content
        tree still fails closed — an empty desired state must never be mistaken for "deploy nothing",
        because for a bundle it reads as "delete the managed content".
        """
        out: list[tuple[object, str, Path, Path, Path]] = []
        seen: dict[str, str] = {}
        for kind in resource_kind.all_kinds():
            for slug, rendered, title, audience in self._artifacts_of(kind):
                # A slug must identify exactly ONE resource across ALL kinds. `resource_kind.slug_for`
                # guarantees disjoint namespaces (`s_*`/`d_*`) only for GENERATED slugs — a PINNED
                # friendly slug carries no prefix, so a collision is reachable via configuration. It
                # would be silently harmful: `resolve_space` builds one dict keyed by slug (the later
                # kind wins the id) while `_kind_of` returns the FIRST matching kind, so a stage would
                # apply Genie's permissions object type / tag entity type to a dashboard's id. Fail
                # loud instead, exactly as a duplicate live title already does.
                if slug in seen:
                    raise ValueError(
                        f"slug {slug!r} is used by both {seen[slug]} and {kind.kind} artifacts; "
                        f"slugs must be unique across resource kinds (refusing to guess)")
                seen[slug] = kind.kind
                out.append((kind, slug, rendered, title, audience))
        if not out:
            raise ValueError("no rendered promotable artifacts found")
        return out

    def _artifacts(self) -> list[tuple[str, Path, Path, Path]]:
        """Back-compat shim: the Genie-only artifact list (kept for callers/tests predating the kind
        seam). New code should use `_all_artifacts`, which covers every kind."""
        return self._artifacts_of(resource_kind.GENIE_SPACE_KIND)

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
        # Per-kind live inventory, read ONCE per kind so a many-artifact promotion doesn't re-list.
        live_by_kind = {
            kind.kind: workspace_resource.list_resources(self.w, kind)
            for kind in resource_kind.all_kinds()
        }
        for kind, slug, rendered, title_path, audience_path in self._all_artifacts():
            title = title_path.read_text(encoding="utf-8").strip()
            matches = [r for r in live_by_kind[kind.kind] if r["title"] == title]
            if len(matches) > 1:
                raise ValueError(f"{slug}: duplicate live title {title!r}")
            if matches:
                # A read-only capability probe for the post-deploy ACL stages: if the SP cannot read
                # this resource's ACL now, it will not be able to reconcile it after the deploy —
                # better to learn that BEFORE any mutation.
                self.w.permissions.get(request_object_type=kind.permissions_object_type,
                                       request_object_id=matches[0]["resource_id"])
            self._run(sys.executable, "scripts/check_audience.py", str(rendered),
                      str(audience_path), "--kind", kind.kind)
            if not kind.has_benchmarks:
                # The dashboard quality floor, asserted BEFORE any mutation: structural integrity
                # offline, then the rendered dataset SQL actually validated against the prod warehouse.
                self._run(sys.executable, "scripts/check_dashboard.py", str(rendered))
                self._run(sys.executable, "scripts/check_dashboard_sql.py", str(rendered),
                          "--warehouse-id", self.warehouse_id)

    def bundle_deploy(self) -> None:
        self._run("databricks", "bundle", "deploy", "-t", "prod", "--var",
                  f"warehouse_id={self.warehouse_id}")

    def _kind_of(self, slug: str):
        """Which kind a slug in ``target_ids`` belongs to.

        Resolved by looking the slug back up in the rendered artifacts rather than by parsing its
        prefix — the artifacts are the authority, and this keeps working for a pinned/friendly slug
        (`receivables`, `recebiveis`) that carries no kind prefix at all.

        Falls back to Genie when the slug matches no rendered artifact. Every slug in ``targets``
        comes from `resolve_space`, which builds it FROM those artifacts, so in a real deploy the
        lookup always hits. The fallback exists for the stage-in-isolation case (a replay or a test
        driving one stage directly) and Genie is the correct default there: it is the historical kind
        every pre-existing deploy target belongs to.
        """
        try:
            artifacts = self._all_artifacts()
        except ValueError:
            return resource_kind.GENIE_SPACE_KIND
        for kind, artifact_slug, _rendered, _title, _audience in artifacts:
            if artifact_slug == slug:
                return kind
        return resource_kind.GENIE_SPACE_KIND

    def resolve_space(self) -> dict[str, str]:
        """Resolve every deployed resource's live id by its declared title, per kind.

        Bounded-retry + refuse-to-guess semantics live in `workspace_resource.resolve_by_title`; both
        kinds get identical treatment, so a duplicate title fails the deploy rather than reconciling
        ACLs onto the wrong object.
        """
        return {
            slug: workspace_resource.resolve_by_title(
                self.w, kind, title.read_text(encoding="utf-8").strip())
            for kind, slug, _rendered, title, _audience in self._all_artifacts()
        }

    def assert_app_manage(self, targets: dict[str, str]) -> None:
        """Grant the app's own service principal CAN_MANAGE on every deployed resource.

        Without this the resource effectively does not exist for the app: the A2 access guard and the
        export both need manage-level reads, and neither Genie nor Lakeview offers a workspace-wide
        listing to a non-admin. Additive and idempotent.
        """
        from databricks.sdk.service import iam
        app_sp = self.w.apps.get(self.app_name).service_principal_client_id
        for slug, resource_id in targets.items():
            self.w.permissions.update(
                request_object_type=self._kind_of(slug).permissions_object_type,
                request_object_id=resource_id,
                access_control_list=[iam.AccessControlRequest(
                    service_principal_name=app_sp,
                    permission_level=iam.PermissionLevel.CAN_MANAGE,
                )],
            )

    def _previous(self, slug: str, kind=None):
        """The PREVIOUS AudienceSpec for a slug, from the pre-merge content checkout.

        Reconciliation needs it to know which principals IT had granted (and may therefore remove) —
        without it a principal dropped from the desired set would linger. Read from the kind's own
        source dir so a dashboard's previous audience is found too.
        """
        if self.previous_content_root is None:
            return None
        rkind = kind or resource_kind.GENIE_SPACE_KIND
        # Reuse the registry's own path builder so the two layouts can never drift apart here.
        path = self.previous_content_root / rkind.audience_path(slug)
        if path.exists():
            with path.open(encoding="utf-8") as handle:
                return audience_spec.parse_sidecar(json.load(handle))
        return None

    def reconcile_audience(self, targets: dict[str, str]) -> None:
        """Converge each deployed resource's app-managed audience to its declared AudienceSpec.

        The derived level is the kind's own (`CAN_RUN` for a Space, `CAN_READ` for a dashboard). A
        principal manually elevated above that level is preserved, never downgraded — see
        `reconcile_audience.desired_acl`.
        """
        for kind, slug, _rendered, _title, audience_path in self._all_artifacts():
            with audience_path.open(encoding="utf-8") as handle:
                desired = audience_spec.parse_sidecar(json.load(handle))
            reconcile_audience.reconcile(
                self.w, targets[slug], desired, previous=self._previous(slug, kind),
                object_type=kind.permissions_object_type, level=kind.audience_level)

    def verify_live_state(self, targets: dict[str, str]) -> None:
        """Read back what was actually granted. Readback — not the write's return — is the evidence."""
        app_sp = self.w.apps.get(self.app_name).service_principal_client_id
        for kind, slug, _rendered, _title, audience_path in self._all_artifacts():
            with audience_path.open(encoding="utf-8") as handle:
                desired = audience_spec.parse_sidecar(json.load(handle))
            acl = self.w.permissions.get(
                request_object_type=kind.permissions_object_type,
                request_object_id=targets[slug]).access_control_list or []
            by_name = {
                reconcile_audience._name(entry): reconcile_audience._direct_level(entry)
                for entry in acl
            }
            if by_name.get(app_sp) not in _TECHNICAL_SUFFICIENT:
                raise RuntimeError(f"{slug}: app service principal CAN_MANAGE readback failed")
            sufficient = _sufficient_for(kind)
            missing = [p.name for p in desired.principals if by_name.get(p.name) not in sufficient]
            if missing:
                raise RuntimeError(f"{slug}: audience readback failed for {', '.join(missing)}")

    def certify_space(self, targets: dict[str, str]) -> None:
        """Assert and read back the governed certification tag for every deployed resource.

        The tag entity type is per kind (`geniespaces` / `dashboards`) — both verified live; the
        singular/alternate spellings are rejected by the API. The readback is retried because tag
        propagation is EVENTUALLY CONSISTENT: the first read after a write can legitimately miss or
        return the old value, and that is not a permission problem. An exhausted budget still fails
        closed.
        """
        from databricks.sdk.errors import NotFound
        from databricks.sdk.service.tags import TagAssignment

        assignments = self.w.workspace_entity_tag_assignments
        for slug, resource_id in targets.items():
            entity_type = self._kind_of(slug).tag_entity_type
            try:
                current = assignments.get_tag_assignment(
                    entity_type, resource_id, _CERTIFICATION_TAG_KEY,
                )
            except NotFound:
                assignments.create_tag_assignment(TagAssignment(
                    entity_type=entity_type,
                    entity_id=resource_id,
                    tag_key=_CERTIFICATION_TAG_KEY,
                    tag_value=_CERTIFICATION_TAG_VALUE,
                ))
            else:
                if current.tag_value != _CERTIFICATION_TAG_VALUE:
                    assignments.update_tag_assignment(
                        entity_type,
                        resource_id,
                        _CERTIFICATION_TAG_KEY,
                        TagAssignment(
                            entity_type=entity_type,
                            entity_id=resource_id,
                            tag_key=_CERTIFICATION_TAG_KEY,
                            tag_value=_CERTIFICATION_TAG_VALUE,
                        ),
                        update_mask="tag_value",
                    )

            for attempt in range(self.certification_readback_max_attempts):
                try:
                    readback = assignments.get_tag_assignment(
                        entity_type, resource_id, _CERTIFICATION_TAG_KEY,
                    )
                except NotFound:
                    if attempt + 1 == self.certification_readback_max_attempts:
                        raise
                else:
                    if readback.tag_value == _CERTIFICATION_TAG_VALUE:
                        break
                    if attempt + 1 == self.certification_readback_max_attempts:
                        raise RuntimeError(f"{slug}: certification tag readback was not certified")
                self.certification_readback_sleep(self.certification_readback_retry_seconds)


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

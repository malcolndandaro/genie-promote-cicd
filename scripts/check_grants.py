"""CI GRANT-01 gate (pr-checks). Does every declared principal have SELECT on every
data_source table of the pre-rendered, prod serialized_space?

This runs in CI, where the workflow authenticates to the PROD workspace as the CI/CD
service principal (DATABRICKS_HOST + oauth-m2m env) — so it has the prod-catalog
visibility the dev-hosted app lacks, and it gates the PR/merge. Exits non-zero on any
GRANT-01 finding (missing or unverifiable grant). The deterministic GRANT-01 logic lives
in genie_reviewer/grant_check.py (shared with the app + the local verify script).

F2: if a per-space `<slug>.access.json` sidecar is committed next to the rendered space (the
app-declared AccessSpec, mirroring the `.title` sidecar), EVERY principal it declares — across
BOTH the Space-permissions and UC-grants halves — is checked, not just the single legacy
CONSUMER_GROUP. A missing sidecar falls back to the original single-group behavior (no AccessSpec
declared yet == the pre-F2 pipeline, still gated on the base consumer group).

G9 (stakeholder decision, 2026-07-13): the base CONSUMER_GROUP and the sidecar's declared
principals are no longer checked as one flat list — `apply_access.py` (post-merge) grants every
DECLARED principal at deploy, so a missing SELECT for one of THEM is a non-blocking warning here
(exit 0), never a reason to block the PR; a missing SELECT for the baseline group still exits 1
unchanged (the pipeline never grants the baseline). A declared GROUP that isn't UC-grantable at all
(workspace-local, e.g. built-in `users`) gets a warning saying so instead of a promise the pipeline
can't keep (`grant_check.is_uc_grantable_group`).

Usage:
    python3 scripts/check_grants.py [rendered_space.json] [consumer_group] [access_sidecar.json]
Local test against a profile:
    DATABRICKS_CONFIG_PROFILE=cerc-mlops-prod python3 scripts/check_grants.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import audience_check  # noqa: E402
import audience_spec  # noqa: E402
import grant_check  # noqa: E402

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import SecurableType

DEFAULT_PATH = "build/genie/receivables.serialized_space.json"


def _load_access_spec(sidecar_path: "str | None") -> access_spec.AccessSpec:
    """The AccessSpec declared for this promotion (F2 sidecar), or the empty spec if none was
    committed — a missing sidecar means the pre-F2 pipeline: only the base consumer group is
    checked (G9 keeps that fallback; it just no longer unions declared principals INTO the base
    group's list — see `main`)."""
    if sidecar_path and os.path.exists(sidecar_path):
        with open(sidecar_path, encoding="utf-8") as fh:
            return access_spec.AccessSpec.from_dict(json.load(fh))
    return access_spec.AccessSpec()


def _group_resource_type(w: WorkspaceClient, display_name: str) -> "str | None":
    """Live SCIM lookup of a group's `meta.resourceType` (`"Group"` = account, `"WorkspaceGroup"` =
    workspace-local) — `None` if the group can't be found at all. Thin wrapper around the SDK; the
    grantability DECISION itself is the pure `grant_check.is_uc_grantable_group`."""
    found = list(w.groups.list(filter=f'displayName eq "{display_name}"'))
    if not found:
        return None
    return getattr(getattr(found[0], "meta", None), "resource_type", None)


def _non_grantable_declared(w: WorkspaceClient, spec: access_spec.AccessSpec) -> list[str]:
    """Which of the spec's declared GROUP principals apply_access could never actually grant
    (workspace-local groups — UC grants reject those). Individual users are never checked here;
    they're always account-level."""
    return [name for name in spec.declared_groups()
            if not grant_check.is_uc_grantable_group(_group_resource_type(w, name), name)]


def _principal_exists(w: WorkspaceClient, name: str, is_group: bool) -> bool:
    return bool(list(w.groups.list(filter=f'displayName eq "{name}"'))) if is_group \
        else bool(list(w.users.list(filter=f'userName eq "{name}"')))


def _canonical_audience_main(space: dict, payload: dict, w: WorkspaceClient) -> int:
    spec = audience_spec.AudienceSpec.from_dict(payload)
    findings = audience_check.check_audience(
        space, spec, lambda fq: _effective_grants(w, fq),
        principal_exists=lambda name, is_group: _principal_exists(w, name, is_group),
        table_exists=lambda fq: bool(w.tables.exists(full_name=fq)),
    )
    for finding in findings:
        level = "error" if finding["severity"] in {"BLOCKER", "OPERATIONAL"} else "warning"
        subject = finding.get("principal") or finding.get("table") or "AUDIENCE-01"
        _emit_annotation(level, f"{subject}: {finding['message']}", title="AUDIENCE-01")
    if any(f["severity"] == "OPERATIONAL" for f in findings):
        return 2
    if any(f["severity"] == "BLOCKER" for f in findings):
        return 1
    print("✅ AUDIENCE-01 — Público do Space válido; missing SELECT é orientação Terraform.")
    return 0


def _gh_escape(s: str) -> str:
    """Escape text for a GitHub Actions workflow-command payload (`::error::`/`::warning::` lines a
    step prints to stdout — GitHub auto-converts them into check-run annotations, which is how the
    app's G8 check-details panel is meant to surface these findings instead of GitHub's own generic
    noise). Per the documented workflow-command escaping, `%` must go first."""
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_annotation(level: str, message: str, *, title: str = "GRANT-01") -> None:
    """Print a GRANT-01 finding as a REAL GitHub annotation (found live: bare `print()`s never
    became annotations, so the app's check-details fallback picked up GitHub's own generic noise —
    a Node.js deprecation warning + "Process completed with exit code 1." — instead of the actual PT
    finding). `level` is `"error"` for a BLOCKER (baseline) finding, `"warning"` for an advisory
    (declared-principal) one — GitHub reports these back as `annotation_level` `failure`/`warning`
    respectively (see `github_app._summarize_annotations`, which prefers the `failure` ones)."""
    print(f"::{level} title={title}::{_gh_escape(message)}")


def _effective_grants(w: WorkspaceClient, full_name: str) -> list[dict]:
    """Effective UC privileges on a table (includes schema/catalog-inherited grants).
    Mirrors app/app_logic.py::_effective_grants; SDK 0.111 wants the string 'TABLE'."""
    resp = w.grants.get_effective(securable_type=SecurableType.TABLE.value, full_name=full_name)
    out = []
    for pa in (resp.privilege_assignments or []):
        privs = [getattr(p.privilege, "value", p.privilege) for p in (pa.privileges or [])]
        out.append({"principal": pa.principal, "privileges": [x for x in privs if x]})
    return out


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    consumer_group = (sys.argv[2] if len(sys.argv) > 2 else None) \
        or os.environ.get("CONSUMER_GROUP", "account users")
    sidecar_path = sys.argv[3] if len(sys.argv) > 3 else None
    if not os.path.exists(path):
        print(f"GRANT-01: nada a checar — {path} não existe (rode scripts/render.sh primeiro).")
        return 0
    with open(path, encoding="utf-8") as fh:
        space = json.load(fh)

    # The current content workflow still passes the third argument under the legacy filename.
    # render.sh may place a canonical AudienceSpec payload there during the atomic switch.
    if sidecar_path and os.path.exists(sidecar_path):
        with open(sidecar_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if "principals" in payload:
            return _canonical_audience_main(space, payload, WorkspaceClient())

    spec = _load_access_spec(sidecar_path)
    declared = list(spec.all_principals())
    w = WorkspaceClient()  # CI: prod SP via env; local: DATABRICKS_CONFIG_PROFILE
    non_grantable = _non_grantable_declared(w, spec) if declared else []
    findings = grant_check.check_grants(
        space, consumer_group, lambda fq: _effective_grants(w, fq),
        declared_principals=declared, non_grantable_principals=non_grantable)

    blockers = [f for f in findings if f.get("severity") == "BLOCKER"]
    advisories = [f for f in findings if f.get("severity") != "BLOCKER"]
    # G9: emit a REAL annotation per finding (alongside the human-readable prints below) — a bare
    # print() never becomes a GitHub check-run annotation, so without this the app's check-details
    # fallback had nothing of ours to show.
    for f in blockers:
        _emit_annotation("error", f"{f.get('table')} / {f.get('principal', consumer_group)}: {f.get('message')}")
    for f in advisories:
        _emit_annotation("warning", f"{f.get('table')} / {f.get('principal')}: {f.get('message')}")
    for f in advisories:
        print(f"🟡 GRANT-01 (advisory, não bloqueia) — {f.get('table')} / {f.get('principal')}: "
              f"{f.get('message')}")
    if blockers:
        print(f"🔴 GRANT-01 — promoção bloqueada ({len(blockers)} achado(s) BLOCKER) "
              f"para o grupo baseline '{consumer_group}':")
        for f in blockers:
            print(f"  - {f.get('table')} / {f.get('principal', consumer_group)}: {f.get('message')}")
        return 1
    if advisories:
        print(f"✅ GRANT-01 — sem BLOCKER; {len(advisories)} achado(s) advisory para os principais "
              f"declarados {declared} (concedidos pelo apply_access no deploy, ou já sinalizados acima "
              f"como não-graváveis).")
        return 0
    print(f"✅ GRANT-01 — todos os principais têm SELECT (baseline '{consumer_group}'"
          + (f" + declarados {declared}" if declared else "") + ") em todas as tabelas do espaço.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

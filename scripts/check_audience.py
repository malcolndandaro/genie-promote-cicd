#!/usr/bin/env python3
"""Authoritative CI AUDIENCE-01 Preflight check."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import audience_check  # noqa: E402
import audience_spec  # noqa: E402

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.catalog import SecurableType  # noqa: E402


def _gh_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _effective_grants(w: WorkspaceClient, full_name: str) -> list[dict]:
    response = w.grants.get_effective(securable_type=SecurableType.TABLE.value, full_name=full_name)
    return [{
        "principal": row.principal,
        "privileges": [
            getattr(p.privilege, "value", p.privilege)
            for p in (row.privileges or []) if p.privilege
        ],
    } for row in (response.privilege_assignments or [])]


def _principal_exists(w: WorkspaceClient, name: str, is_group: bool) -> bool:
    if is_group:
        return bool(list(w.groups.list(filter=f'displayName eq "{name}"')))
    return bool(list(w.users.list(filter=f'userName eq "{name}"')))


def _load(path: str) -> audience_spec.AudienceSpec:
    with open(path, encoding="utf-8") as handle:
        return audience_spec.parse_sidecar(
            json.load(handle), warn=lambda message: print(f"::warning title=AudienceSpec legacy::{_gh_escape(message)}")
        )


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: check_audience.py <rendered_space.json> <audience.json>", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as handle:
        space = json.load(handle)
    spec = _load(sys.argv[2])
    w = WorkspaceClient()
    findings = audience_check.check_audience(
        space, spec, lambda table: _effective_grants(w, table),
        principal_exists=lambda name, is_group: _principal_exists(w, name, is_group),
        table_exists=lambda table: bool(w.tables.exists(full_name=table)),
    )
    for finding in findings:
        severity = finding["severity"]
        level = "error" if severity in {"BLOCKER", "OPERATIONAL"} else "warning"
        subject = finding.get("principal") or finding.get("table") or "AUDIENCE-01"
        print(f"::{level} title=AUDIENCE-01::{_gh_escape(f'{subject}: {finding["message"]}')}")
    blockers = [f for f in findings if f["severity"] == "BLOCKER"]
    operational = [f for f in findings if f["severity"] == "OPERATIONAL"]
    if operational:
        print(f"AUDIENCE-01 operational failure: {len(operational)} finding(s)")
        return 2
    if blockers:
        print(f"AUDIENCE-01 blocked: {len(blockers)} invalid principal/table finding(s)")
        return 1
    advisories = [f for f in findings if f["severity"] == "SUGGESTION"]
    print(f"AUDIENCE-01 passed with {len(advisories)} informational missing-SELECT notice(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

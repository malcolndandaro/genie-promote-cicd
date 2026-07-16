#!/usr/bin/env python3
"""Converge only app-managed Genie audience ACLs to AudienceSpec and verify live state."""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import audience_spec  # noqa: E402

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel  # noqa: E402

_SUFFICIENT = {"CAN_RUN", "CAN_EDIT", "CAN_MANAGE", "IS_OWNER"}


def _name(entry) -> str | None:
    return entry.user_name or entry.group_name or entry.service_principal_name


def _direct_level(entry) -> str | None:
    for permission in entry.all_permissions or []:
        if not getattr(permission, "inherited", False) and permission.permission_level:
            return getattr(permission.permission_level, "value", permission.permission_level)
    return None


def _request(name: str, is_group: bool, level: str) -> AccessControlRequest:
    kwargs = {"permission_level": PermissionLevel(level)}
    kwargs["group_name" if is_group else "user_name"] = name
    return AccessControlRequest(**kwargs)


def desired_acl(current: Iterable, desired: audience_spec.AudienceSpec,
                previous: audience_spec.AudienceSpec | None = None) -> list[AccessControlRequest]:
    """Build a complete replacement ACL while preserving everything the app does not own.

    A removed previously-managed principal is deleted only when its direct level is exactly
    ``CAN_RUN``; a manual elevation (CAN_EDIT/MANAGE/OWNER) is preserved. Desired principals keep a
    stronger existing direct level and otherwise converge to CAN_RUN.
    """
    desired_by_name = {p.name.casefold(): p for p in desired.principals}
    previous_names = {p.name.casefold() for p in previous.principals} if previous else set()
    out: list[AccessControlRequest] = []
    present: set[str] = set()
    for entry in current:
        name = _name(entry)
        level = _direct_level(entry)
        if not name or not level:  # inherited-only entries are not part of the replace payload
            continue
        key = name.casefold()
        wanted = desired_by_name.get(key)
        if wanted:
            present.add(key)
            out.append(_request(name, wanted.is_group, level if level in _SUFFICIENT else "CAN_RUN"))
            continue
        if key in previous_names and level == "CAN_RUN":
            continue  # remove only the exact direct grant this app previously managed
        out.append(_request(name, bool(entry.group_name), level))
    for key, principal in desired_by_name.items():
        if key not in present:
            out.append(_request(principal.name, principal.is_group, "CAN_RUN"))
    return out


def reconcile(w: WorkspaceClient, space_id: str, desired: audience_spec.AudienceSpec,
              previous: audience_spec.AudienceSpec | None = None) -> dict:
    before = w.permissions.get(request_object_type="genie", request_object_id=space_id)
    acl = desired_acl(before.access_control_list or [], desired, previous)
    w.permissions.set(request_object_type="genie", request_object_id=space_id,
                      access_control_list=acl)
    after = w.permissions.get(request_object_type="genie", request_object_id=space_id)
    by_name = {_name(entry): _direct_level(entry) for entry in (after.access_control_list or [])}
    missing = [p.name for p in desired.principals if by_name.get(p.name) not in _SUFFICIENT]
    if missing:
        raise RuntimeError(f"audience readback missing CAN_RUN for: {', '.join(missing)}")
    return {"space_id": space_id, "principals": list(desired.names()), "verified": True}


def _load(path: str | None) -> audience_spec.AudienceSpec | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return audience_spec.parse_sidecar(json.load(handle), warn=lambda m: print(f"::warning::{m}"))


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: reconcile_audience.py <audience.json> <space_id> [previous-audience.json]",
              file=sys.stderr)
        return 2
    desired = _load(sys.argv[1])
    if desired is None:
        raise ValueError("AudienceSpec sidecar is required")
    previous = _load(sys.argv[3]) if len(sys.argv) > 3 else None
    result = reconcile(WorkspaceClient(), sys.argv[2], desired, previous)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

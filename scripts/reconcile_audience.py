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

# Levels that count as "this principal can use the resource". CAN_READ is the AI/BI dashboard audience
# level; Genie issues no level below CAN_RUN, so including it widens nothing for Spaces.
_SUFFICIENT = {"CAN_READ", "CAN_RUN", "CAN_EDIT", "CAN_MANAGE", "IS_OWNER"}
# The default derived level (Genie). A dashboard caller passes level="CAN_READ".
_DEFAULT_LEVEL = "CAN_RUN"
_DEFAULT_OBJECT_TYPE = "genie"

# Ordering of the levels this app may derive, weakest first — used to decide whether an EXISTING grant
# is already at least as strong as the level we would derive. This replaces a plain `in _SUFFICIENT`
# test, which was correct while CAN_RUN was the only derived level but would now treat a dashboard's
# CAN_READ as "strong enough" for a Genie space's CAN_RUN.
_LEVEL_RANK = {"CAN_READ": 1, "CAN_RUN": 2, "CAN_EDIT": 3, "CAN_MANAGE": 4, "IS_OWNER": 5}


def _at_least(level: str | None, floor: str) -> bool:
    """Whether an existing direct ``level`` is at least as strong as ``floor``."""
    return _LEVEL_RANK.get(level or "", 0) >= _LEVEL_RANK.get(floor, 0)


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
                previous: audience_spec.AudienceSpec | None = None,
                level: str = _DEFAULT_LEVEL) -> list[AccessControlRequest]:
    """Build a complete replacement ACL while preserving everything the app does not own.

    A removed previously-managed principal is deleted only when its direct level is exactly the level
    THIS APP DERIVES (``level``); a manual elevation above that is preserved. Desired principals keep
    a stronger existing direct level and otherwise converge to ``level``.

    ``level`` is the kind's derived audience level (`CAN_RUN` for a Genie Space, `CAN_READ` for an
    AI/BI dashboard). It defaults to Genie's so every pre-existing caller is unchanged.
    """
    desired_by_name = {p.name.casefold(): p for p in desired.principals}
    previous_names = {p.name.casefold() for p in previous.principals} if previous else set()
    out: list[AccessControlRequest] = []
    present: set[str] = set()
    for entry in current:
        name = _name(entry)
        current_level = _direct_level(entry)
        if not name or not current_level:  # inherited-only entries are not part of the replace payload
            continue
        key = name.casefold()
        wanted = desired_by_name.get(key)
        if wanted:
            present.add(key)
            # Keep an existing grant that is already at least as strong as what we would derive;
            # otherwise converge up to the derived level. Never downgrade.
            keep = current_level if _at_least(current_level, level) else level
            out.append(_request(name, wanted.is_group, keep))
            continue
        if key in previous_names and current_level == level:
            continue  # remove only the exact direct grant this app previously managed
        out.append(_request(name, bool(entry.group_name), current_level))
    for key, principal in desired_by_name.items():
        if key not in present:
            out.append(_request(principal.name, principal.is_group, level))
    return out


def reconcile(w: WorkspaceClient, space_id: str, desired: audience_spec.AudienceSpec,
              previous: audience_spec.AudienceSpec | None = None,
              object_type: str = _DEFAULT_OBJECT_TYPE, level: str = _DEFAULT_LEVEL) -> dict:
    """Converge one resource's app-managed audience, then PROVE it by reading the ACL back.

    ``object_type``/``level`` are the kind's own (`genie`/`CAN_RUN` or `dashboards`/`CAN_READ`),
    defaulting to Genie's so existing callers are unchanged. The readback — not the write's return
    value — is the evidence a deploy reports.
    """
    before = w.permissions.get(request_object_type=object_type, request_object_id=space_id)
    acl = desired_acl(before.access_control_list or [], desired, previous, level=level)
    w.permissions.set(request_object_type=object_type, request_object_id=space_id,
                      access_control_list=acl)
    after = w.permissions.get(request_object_type=object_type, request_object_id=space_id)
    by_name = {_name(entry): _direct_level(entry) for entry in (after.access_control_list or [])}
    missing = [p.name for p in desired.principals if not _at_least(by_name.get(p.name), level)]
    if missing:
        raise RuntimeError(f"audience readback missing {level} for: {', '.join(missing)}")
    return {"space_id": space_id, "principals": list(desired.names()), "verified": True}


def _load(path: str | None) -> audience_spec.AudienceSpec | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return audience_spec.parse_sidecar(json.load(handle))


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: reconcile_audience.py <audience.json> <resource_id> [previous-audience.json] "
              "[--kind genie_space|dashboard]", file=sys.stderr)
        return 2
    argv = [a for a in sys.argv[1:] if not a.startswith("--kind")]
    kind_args = [a for a in sys.argv[1:] if a.startswith("--kind=")]
    kind_name = kind_args[-1].split("=", 1)[1] if kind_args else None
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "genie_reviewer"))
    import resource_kind  # noqa: PLC0415 — CLI-only import, keeps the library half dependency-free

    kind = resource_kind.get(kind_name)
    desired = _load(argv[0])
    if desired is None:
        raise ValueError("AudienceSpec sidecar is required")
    previous = _load(argv[2]) if len(argv) > 2 else None
    result = reconcile(WorkspaceClient(), argv[1], desired, previous,
                       object_type=kind.permissions_object_type, level=kind.audience_level)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

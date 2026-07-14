#!/usr/bin/env python3
"""apply_access.py — GOVERNED enforcement of an author-declared AccessSpec (F2).

Runs in CI (pr-checks preview / deploy, as the prod SP) — NEVER app-direct (the app only
DECLARES the AccessSpec into the per-space `.access.json` sidecar; this script is what actually
touches prod permissions/grants, same split as `check_grants.py`/GRANT-01).

Idempotent + imperative (mirrors `src/setup/seed_recebiveis.py`'s CREATE-IF-NOT-EXISTS style),
deliberately NOT a declarative DABs grants resource — a redeploy must be a safe no-op, not a
`SCHEMA_ALREADY_EXISTS`-style failure (the F2 acceptance criteria).

DESIGN (changed 2026-07-14, live prod failure — see the git history for the prior per-Space-group
version): access is applied DIRECTLY to each declared principal, never via an intermediate group.
  1. GRANT SELECT (+ USE_CATALOG/USE_SCHEMA, additive) on every data_source table's catalog/schema
     to EACH declared principal — idempotent (`GRANT` is a no-op if already held). A declared GROUP
     is verified live and SKIPPED (loud warning, never a crash) if it isn't UC-grantable.
  2. apply the Genie Space permission entries (CAN_RUN/CAN_VIEW) directly to each declared
     principal — additive (`w.permissions.update`, not `.set`, so an existing ACL entry for
     someone else is preserved).

WHY NOT a per-Space group (the prior design): `w.groups.create` in a workspace context creates a
WORKSPACE-LOCAL group, and UC grants only accept ACCOUNT-level principals — proven live
(`NotFound: Could not find principal with name grp_genie_s_...`) on a real prod deploy. The CI SP
is workspace admin, not account admin, so it cannot create an account-level group to route grants
through. Direct-to-principal grants sidestep the problem entirely: an individual user/SP is always
account-level (always grantable); a declared GROUP might itself be workspace-local (e.g. the
built-in `users`), in which case its UC grant is skipped — with a loud PT warning, never silently
— while its Genie Space permission (a workspace-level ACL, which DOES accept workspace-local
groups) is still applied.

Usage:
    python3 scripts/apply_access.py <rendered_space.json> <access_sidecar.json> <space_id>
Local test against a profile:
    DATABRICKS_CONFIG_PROFILE=cerc-mlops-prod python3 scripts/apply_access.py ...
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import grant_check  # noqa: E402

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.catalog import PermissionsChange, Privilege  # noqa: E402
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel  # noqa: E402


def _table_containers(space: dict) -> set[tuple[str, str]]:
    """(catalog, schema) pairs for every data_source table — SELECT is granted at schema level
    (mirrors seed_recebiveis.py: cascades to current + future tables in that schema)."""
    out = set()
    for t in (space.get("data_sources") or {}).get("tables", []) or []:
        parts = (t.get("identifier") or "").split(".")
        if len(parts) == 3:
            out.add((parts[0], parts[1]))
    return out


def _group_resource_type(w: WorkspaceClient, display_name: str) -> "str | None":
    """Live SCIM lookup of a group's `meta.resourceType` (`"Group"` = account, `"WorkspaceGroup"` =
    workspace-local) — `None` if the group can't be found at all. Thin wrapper around the SDK; the
    grantability DECISION itself is the pure `grant_check.is_uc_grantable_group` (mirrors
    `check_grants.py`'s identical helper, kept separate — this script has no import on that one)."""
    found = list(w.groups.list(filter=f'displayName eq "{display_name}"'))
    if not found:
        return None
    return getattr(getattr(found[0], "meta", None), "resource_type", None)


def apply_uc_grants(w: WorkspaceClient, principals: list[access_spec.Principal],
                    containers: set[tuple[str, str]]) -> None:
    """GRANT USE_CATALOG/USE_SCHEMA/SELECT on each table's (catalog, schema) DIRECTLY to each
    declared principal. Idempotent: re-applying an already-held grant is a no-op (mirrors
    seed_recebiveis.py). A declared GROUP that UC grants would reject (workspace-local — verified
    live via `_group_resource_type` + `grant_check.is_uc_grantable_group`) is SKIPPED with a loud
    PT warning line, never a crash: its Genie Space permission (applied separately, see
    `apply_space_permissions`) still goes through, so the principal isn't silently locked out of
    the Space entirely, just this one grant. An individual user/SP is always account-level and is
    never skipped."""
    grantable: list[str] = []
    for p in principals:
        if p.is_group and not grant_check.is_uc_grantable_group(_group_resource_type(w, p.name), p.name):
            print(f"::warning title=apply-access::'{p.name}' é um grupo local do workspace — "
                  f"grant UC pulado; permissão do Space aplicada")
            continue
        grantable.append(p.name)
    if not grantable:
        return
    for catalog, schema in sorted(containers):
        for name in grantable:
            # Typed PermissionsChange (NOT a raw dict — grants.update calls `.as_dict()` on each
            # change before serializing, which raises AttributeError on a dict).
            w.grants.update(
                securable_type="catalog", full_name=catalog,
                changes=[PermissionsChange(principal=name, add=[Privilege.USE_CATALOG])])
            w.grants.update(
                securable_type="schema", full_name=f"{catalog}.{schema}",
                changes=[PermissionsChange(principal=name, add=[Privilege.USE_SCHEMA, Privilege.SELECT])])
        print(f"granted USE_CATALOG+USE_SCHEMA+SELECT on {catalog}.{schema} to {len(grantable)} principal(s)")


def resolve_space_id(w: WorkspaceClient, title: str) -> str:
    """Resolve the deployed PROD Genie Space id by matching its title (deterministic per slug — set
    by the promotion's .title sidecar / the genie_spaces resource). The live id is assigned by Genie
    at deploy and is NOT in the committed serialized_space, and `bundle summary` does NOT expose it
    for a genie_spaces resource either (verified live, 2026-07-14: its resource entry carries only
    file_path/title/warehouse_id/serialized_space, no id) — so match on title (same pattern
    app_logic.list_spaces uses). Fails LOUD both when NOT FOUND and on an AMBIGUOUS match (more than
    one deployed Space sharing the title): a warn-and-skip, or a silent pick of the first match,
    would risk applying access to the WRONG Space — never guess which one was meant."""
    if not title:
        raise ValueError("apply_access: no title provided — cannot resolve the deployed Space id")
    matches = [s.space_id for s in (w.genie.list_spaces().spaces or []) if (s.title or "") == title]
    if not matches:
        raise ValueError(f"apply_access: no deployed Genie Space found with title {title!r} "
                         f"— cannot apply declared access")
    if len(matches) > 1:
        raise ValueError(f"apply_access: {len(matches)} deployed Genie Spaces share the title "
                         f"{title!r} (ids={matches}) — refusing to guess which one to apply access to")
    return matches[0]


def apply_space_permissions(w: WorkspaceClient, space_id: str,
                            entries: list[access_spec.SpacePermission]) -> None:
    """Additive Genie Space ACL update — each declared principal gets ITS OWN declared level
    (CAN_RUN/CAN_VIEW), applied directly to that user/group, NOT collapsed onto a single group at
    the max level (which would silently upgrade a CAN_VIEW principal to CAN_RUN). Uses
    `permissions.update` (additive) with one ACL entry per principal — never `.set` (which would
    REPLACE the whole ACL and could drop an existing owner/CAN_MANAGE entry)."""
    if not entries:
        return
    acl = []
    for e in entries:
        level = PermissionLevel(e.level)  # "CAN_RUN"/"CAN_VIEW" -> enum by value
        if e.principal.is_group:
            acl.append(AccessControlRequest(group_name=e.principal.name, permission_level=level))
        else:
            acl.append(AccessControlRequest(user_name=e.principal.name, permission_level=level))
    w.permissions.update(request_object_type="genie", request_object_id=space_id,
                         access_control_list=acl)
    for e in entries:
        print(f"applied Genie Space permission {e.level} to {e.principal.name} on {space_id}")


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: apply_access.py <rendered_space.json> <access_sidecar.json> <space_title>",
              file=sys.stderr)
        return 2
    # arg 3 is the deployed Space TITLE (from the <slug>.title sidecar) — the live id is resolved
    # here via genie.list_spaces() (it's not in bundle summary / the committed serialized_space).
    rendered_path, sidecar_path, title = sys.argv[1], sys.argv[2], sys.argv[3]
    if not os.path.exists(sidecar_path):
        print(f"apply_access: no sidecar at {sidecar_path} — nothing declared, skipping.")
        return 0
    with open(sidecar_path, encoding="utf-8") as fh:
        spec = access_spec.AccessSpec.from_dict(json.load(fh))
    if spec.is_empty():
        print("apply_access: AccessSpec is empty — nothing to apply.")
        return 0
    with open(rendered_path, encoding="utf-8") as fh:
        space = json.load(fh)

    slug = os.path.basename(sidecar_path).removesuffix(".access.json")

    w = WorkspaceClient()  # CI: prod SP via env; local: DATABRICKS_CONFIG_PROFILE
    all_principals = [
        *(sp.principal for sp in spec.space_permissions),
        *spec.uc_principals,
    ]
    # dedupe while preserving Principal objects (a plain set() would need Principal to be
    # hashable by identity only; names are the real dedupe key here)
    seen: dict[str, access_spec.Principal] = {}
    for p in all_principals:
        seen.setdefault(p.name, p)
    apply_uc_grants(w, list(seen.values()), _table_containers(space))
    if spec.space_permissions:
        # Only resolve the Space id when Space permissions are declared (UC-only specs never touch
        # the Space ACL). Fail loud if the deployed Space can't be found (never warn-and-skip).
        space_id = resolve_space_id(w, title)
        apply_space_permissions(w, space_id, list(spec.space_permissions))
        print(f"apply_access: done for space='{title}' (id={space_id}) slug={slug}")
    else:
        print(f"apply_access: UC grants only (no Space permissions) for slug={slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

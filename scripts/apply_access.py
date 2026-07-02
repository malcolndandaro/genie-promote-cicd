#!/usr/bin/env python3
"""apply_access.py — GOVERNED enforcement of an author-declared AccessSpec (F2).

Runs in CI (pr-checks preview / deploy, as the prod SP) — NEVER app-direct (the app only
DECLARES the AccessSpec into the per-space `.access.json` sidecar; this script is what actually
touches prod permissions/grants, same split as `check_grants.py`/GRANT-01).

Idempotent + imperative (mirrors `src/setup/seed_recebiveis.py`'s CREATE-IF-NOT-EXISTS style),
deliberately NOT a declarative DABs grants resource — a redeploy must be a safe no-op, not a
`SCHEMA_ALREADY_EXISTS`-style failure (GRANT-02 + the F2 acceptance criteria).

Access is applied via ONE per-Space GROUP (`access_spec.group_name_for(slug)`), never scattered
per-user grants (GRANT-02):
  1. get-or-create the account-level group (idempotent: `w.groups.list(filter=...)` first).
  2. reconcile membership: add any declared principal (user OR group) not already a member; a
     principal that is itself declared as a group is added as a nested group member so its own
     members inherit access transitively (Databricks SCIM groups nest).
  3. GRANT SELECT (+ USE_CATALOG/USE_SCHEMA, additive) on every data_source table's catalog/schema
     to the per-Space group — idempotent (`GRANT` is a no-op if already held).
  4. apply the Genie Space permission entries (CAN_RUN/CAN_VIEW) — additive
     (`w.permissions.update`, not `.set`, so an existing ACL entry for someone else is preserved).

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

from databricks.sdk import WorkspaceClient  # noqa: E402
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


def get_or_create_group(w: WorkspaceClient, display_name: str):
    """Idempotent get-or-create of the per-Space account group (GRANT-02)."""
    existing = list(w.groups.list(filter=f'displayName eq "{display_name}"'))
    if existing:
        return existing[0]
    print(f"creating group {display_name}")
    return w.groups.create(display_name=display_name)


def _resolve_principal_id(w: WorkspaceClient, principal: access_spec.Principal) -> str:
    """Resolve a declared principal (user email OR group displayName) to its numeric Databricks id.
    A SCIM group member's `value` MUST be the principal's id — NOT its name/email (a name is either
    rejected by the SCIM API or silently references nobody, so the grant never takes effect). Fail
    loud if unresolved: a silent skip would report success while granting access to no one."""
    if principal.is_group:
        found = list(w.groups.list(filter=f'displayName eq "{principal.name}"'))
        if found and found[0].id:
            return found[0].id
    else:
        found = list(w.users.list(filter=f'userName eq "{principal.name}"'))
        if found and found[0].id:
            return found[0].id
    raise ValueError(
        f"apply_access: could not resolve {'group' if principal.is_group else 'user'} "
        f"'{principal.name}' to a Databricks id — refusing to write a bogus group membership")


def reconcile_membership(w: WorkspaceClient, group, principals: list[access_spec.Principal]) -> None:
    """Add any declared principal not already a member, referencing each by its numeric id (SCIM
    member `value` must be an id, not a name). Idempotent on the member-id SET — re-running with the
    same AccessSpec is a no-op. Never REMOVES a member — F2 only grows access via this path;
    revocation is a separate, explicit governed change (out of scope here, same as GRANT-02)."""
    current_ids = {m.value for m in (group.members or []) if getattr(m, "value", None)}
    to_add: list[tuple[access_spec.Principal, str]] = []
    for p in principals:
        pid = _resolve_principal_id(w, p)  # raises (fail-loud) if a declared principal can't resolve
        if pid not in current_ids:
            to_add.append((p, pid))
    if not to_add:
        print(f"group {group.display_name}: membership already up to date ({len(current_ids)} member(s))")
        return
    for p, pid in to_add:
        kind = "group" if p.is_group else "user/service-principal"
        print(f"group {group.display_name}: adding {kind} '{p.name}' (id={pid})")
    w.groups.patch(group.id, operations=[{
        "op": "add", "path": "members",
        "value": [{"value": pid} for _, pid in to_add],  # SCIM member ref = numeric id, not name
    }])


def apply_uc_grants(w: WorkspaceClient, group_name: str, containers: set[tuple[str, str]]) -> None:
    """GRANT USE_CATALOG/USE_SCHEMA/SELECT on each table's (catalog, schema) to the per-Space group.
    Idempotent: re-applying an already-held grant is a no-op (mirrors seed_recebiveis.py)."""
    for catalog, schema in sorted(containers):
        w.grants.update(
            securable_type="catalog", full_name=catalog,
            changes=[{"principal": group_name, "add": ["USE_CATALOG"]}])
        w.grants.update(
            securable_type="schema", full_name=f"{catalog}.{schema}",
            changes=[{"principal": group_name, "add": ["USE_SCHEMA", "SELECT"]}])
        print(f"granted USE_SCHEMA+SELECT on {catalog}.{schema} to {group_name}")


def resolve_space_id(w: WorkspaceClient, title: str) -> str:
    """Resolve the deployed PROD Genie Space id by matching its title (deterministic per slug — set
    by the promotion's .title sidecar / the genie_spaces resource). The live id is assigned by Genie
    at deploy and is NOT in the committed serialized_space, and `bundle summary` does NOT expose it
    for a genie_spaces resource — so match on title (same pattern app_logic.list_spaces uses). Fail
    LOUD if not found: a warn-and-skip would silently never apply declared access (defeats F2)."""
    if not title:
        raise ValueError("apply_access: no title provided — cannot resolve the deployed Space id")
    for s in (w.genie.list_spaces().spaces or []):
        if (s.title or "") == title:
            return s.space_id
    raise ValueError(f"apply_access: no deployed Genie Space found with title {title!r} "
                     f"— cannot apply declared access")


def apply_space_permissions(w: WorkspaceClient, space_id: str,
                            entries: list[access_spec.SpacePermission]) -> None:
    """Additive Genie Space ACL update — each declared principal gets ITS OWN declared level
    (CAN_RUN/CAN_VIEW), applied directly to that user/group, NOT collapsed onto a single group at
    the max level (which would silently upgrade a CAN_VIEW principal to CAN_RUN). Uses
    `permissions.update` (additive) with one ACL entry per principal — never `.set` (which would
    REPLACE the whole ACL and could drop an existing owner/CAN_MANAGE entry). (The per-Space GROUP
    still carries the UC SELECT data grants — GRANT-02 is about table grants, not the Space ACL.)"""
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
    group_name = access_spec.group_name_for(slug)

    w = WorkspaceClient()  # CI: prod SP via env; local: DATABRICKS_CONFIG_PROFILE
    group = get_or_create_group(w, group_name)
    all_principals = [
        *(sp.principal for sp in spec.space_permissions),
        *spec.uc_principals,
    ]
    # dedupe while preserving Principal objects (a plain set() would need Principal to be
    # hashable by identity only; names are the real dedupe key here)
    seen: dict[str, access_spec.Principal] = {}
    for p in all_principals:
        seen.setdefault(p.name, p)
    reconcile_membership(w, group, list(seen.values()))
    apply_uc_grants(w, group_name, _table_containers(space))
    if spec.space_permissions:
        # Only resolve the Space id when Space permissions are declared (UC-only specs never touch
        # the Space ACL). Fail loud if the deployed Space can't be found (never warn-and-skip).
        space_id = resolve_space_id(w, title)
        apply_space_permissions(w, space_id, list(spec.space_permissions))
        print(f"apply_access: done for space='{title}' (id={space_id}) slug={slug} group={group_name}")
    else:
        print(f"apply_access: UC grants only (no Space permissions) for slug={slug} group={group_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

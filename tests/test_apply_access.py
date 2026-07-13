"""Unit tests for scripts/apply_access.py — the GOVERNED (CI-run, prod-SP) enforcement of an
AccessSpec (F2). Fakes stand in for the SDK (`w.groups`, `w.users`, `w.grants`, `w.permissions`,
`w.genie`) so the idempotent get-or-create / id-resolved membership / grant-apply / title->id logic
is unit-tested with no live workspace. These fakes deliberately model the REAL SDK shapes the
independent review flagged: a SCIM group member is referenced by numeric id (not name), and a
deployed Space id is resolved via genie.list_spaces() (not bundle summary).
"""
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import apply_access  # noqa: E402
from databricks.sdk.service.iam import PermissionLevel  # noqa: E402


class FakeGroups:
    """In-memory account-group store. Members are held by numeric id (SCIM `value` is an id, not a
    name) — mirroring the real API, so a name-as-value bug can't pass these tests.

    `patch()` calls `.as_dict()` on each operation and requires a `schemas` kwarg, exactly like the
    real `GroupsAPI.patch` — a caller passing a raw dict (instead of a typed `iam.Patch`) blows up
    here with the SAME `AttributeError` the real SDK raises, so this class of bug can't silently
    pass the suite again."""

    def __init__(self, preexisting=None):
        self.groups: dict[str, NS] = {}   # display_name -> NS(id, display_name, members)
        for name, gid in (preexisting or {}).items():   # groups that already exist (declared groups)
            self.groups[name] = NS(id=gid, display_name=name, members=[])
        self.create_calls = 0
        self.patch_calls = []

    def list(self, filter: str):  # noqa: A002 - mirrors the SDK kwarg
        name = filter.split('"')[1]  # `displayName eq "<name>"`
        g = self.groups.get(name)
        return [g] if g else []

    def create(self, *, display_name: str):
        self.create_calls += 1
        g = NS(id=f"gid-{display_name}", display_name=display_name, members=[])
        self.groups[display_name] = g
        return g

    def patch(self, group_id: str, *, operations: list, schemas: list):
        assert schemas, "groups.patch must be called with the SCIM PatchOp schema"
        serialized = [op.as_dict() for op in operations]  # raises AttributeError on a raw dict
        self.patch_calls.append((group_id, serialized))
        group = next(g for g in self.groups.values() if g.id == group_id)
        for op in serialized:
            if op["op"] == "add":
                members = (op.get("value") or {}).get("members", [])
                existing = {m.value for m in group.members}
                for v in members:
                    if v["value"] not in existing:
                        group.members.append(NS(value=v["value"], display=v["value"]))


class FakeUsers:
    """`list(filter='userName eq "..."')` -> [NS(id=...)]. By default every user resolves to a
    synthetic id; `unknown` names resolve to [] (to test the fail-loud path)."""

    def __init__(self, unknown=None):
        self.unknown = set(unknown or [])

    def list(self, filter: str):  # noqa: A002
        name = filter.split('"')[1]
        return [] if name in self.unknown else [NS(id=f"uid-{name}")]


class FakeGrants:
    """`update()` calls `.as_dict()` on each change, exactly like the real `GrantsAPI.update` — a
    caller passing a raw dict (instead of a typed `catalog.PermissionsChange`) blows up here with
    the same `AttributeError` the real SDK raises."""

    def __init__(self):
        self.calls = []

    def update(self, *, securable_type, full_name, changes):
        serialized = [c.as_dict() for c in changes]  # raises AttributeError on a raw dict
        self.calls.append({"securable_type": securable_type, "full_name": full_name, "changes": serialized})


class FakePermissions:
    def __init__(self):
        self.calls = []

    def update(self, *, request_object_type, request_object_id, access_control_list):
        self.calls.append({
            "request_object_type": request_object_type, "request_object_id": request_object_id,
            "access_control_list": access_control_list,
        })


class FakeGenie:
    def __init__(self, spaces):   # list of (space_id, title)
        self._spaces = spaces

    def list_spaces(self):
        return NS(spaces=[NS(space_id=sid, title=t) for sid, t in self._spaces])


def fake_client(*, groups_pre=None, users=None, spaces=None):
    return NS(groups=FakeGroups(groups_pre), grants=FakeGrants(), permissions=FakePermissions(),
              users=users or FakeUsers(), genie=FakeGenie(spaces or []))


SPACE = {"data_sources": {"tables": [
    {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
    {"identifier": "prod_recebiveis.diamond.dim_arranjo"},
]}}


# --- group get-or-create ---------------------------------------------------------------------

def test_get_or_create_group_is_idempotent():
    w = fake_client()
    g1 = apply_access.get_or_create_group(w, "grp_genie_receivables")
    g2 = apply_access.get_or_create_group(w, "grp_genie_receivables")
    assert g1.id == g2.id
    assert w.groups.create_calls == 1  # second call found the existing group


# --- membership: id-resolved (BLOCKER 1) -----------------------------------------------------

def test_reconcile_membership_references_members_by_ID_not_name():
    # BLOCKER 1: a SCIM member `value` MUST be the numeric id, not the email/displayName.
    w = fake_client()
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    apply_access.reconcile_membership(w, group, [access_spec.Principal("ana@x.com")])
    added = w.groups.patch_calls[0][1][0]["value"]["members"]
    assert added == [{"value": "uid-ana@x.com"}]      # resolved id, NOT "ana@x.com"
    assert added[0]["value"] != "ana@x.com"


def test_reconcile_membership_resolves_a_group_principal_via_groups_api():
    w = fake_client(groups_pre={"data_analysts": "gid-existing-42"})
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    apply_access.reconcile_membership(w, group, [access_spec.Principal("data_analysts", is_group=True)])
    assert w.groups.patch_calls[-1][1][0]["value"]["members"] == [{"value": "gid-existing-42"}]


def test_reconcile_membership_issues_one_patch_call_per_principal():
    # A failure on one principal must be attributable to exactly that principal — so adds are
    # issued as separate `groups.patch` calls, never batched into one operation.
    w = fake_client()
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    apply_access.reconcile_membership(
        w, group,
        [access_spec.Principal("ana@x.com"), access_spec.Principal("bob@x.com")])
    assert len(w.groups.patch_calls) == 2
    added_ids = {c[1][0]["value"]["members"][0]["value"] for c in w.groups.patch_calls}
    assert added_ids == {"uid-ana@x.com", "uid-bob@x.com"}


def test_reconcile_membership_names_the_failing_principal_on_error():
    # A patch failure must be re-raised naming the specific principal that failed, not a bare
    # SDK error — this is what makes a real-workspace failure attributable in the CI log.
    w = fake_client()
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated API failure")

    w.groups.patch = boom
    with pytest.raises(RuntimeError, match="bob@x.com"):
        apply_access.reconcile_membership(w, group, [access_spec.Principal("bob@x.com")])


def test_reconcile_membership_is_idempotent_on_the_member_id_set():
    w = fake_client()
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    group.members = [NS(value="uid-ana@x.com", display="ana@x.com")]
    apply_access.reconcile_membership(w, group, [access_spec.Principal("ana@x.com")])
    assert w.groups.patch_calls == []  # already a member (by id) -> no API call


def test_reconcile_membership_fails_loud_on_unresolvable_principal():
    w = fake_client(users=FakeUsers(unknown={"ghost@x.com"}))
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    with pytest.raises(ValueError):
        apply_access.reconcile_membership(w, group, [access_spec.Principal("ghost@x.com")])


def test_reconcile_membership_passes_typed_patch_objects_not_raw_dicts():
    """Root-cause regression for the live crash (prod deploy run 29282475355):
    `AttributeError: 'dict' object has no attribute 'as_dict'` — groups.patch was called with a
    raw dict operation. The real SDK calls `.as_dict()` on each operation before serializing, so
    every operation passed here must be a typed model (`iam.Patch`), never a plain dict."""
    captured_ops: list = []
    w = fake_client()
    real_patch = w.groups.patch

    def spy_patch(group_id, *, operations, schemas):
        captured_ops.extend(operations)
        return real_patch(group_id, operations=operations, schemas=schemas)

    w.groups.patch = spy_patch
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    apply_access.reconcile_membership(w, group, [access_spec.Principal("ana@x.com")])
    assert captured_ops
    for op in captured_ops:
        assert not isinstance(op, dict)
        assert hasattr(op, "as_dict"), f"expected a typed SDK model, got {type(op)}"


# --- UC grants -------------------------------------------------------------------------------

def test_apply_uc_grants_is_schema_scoped_and_additive():
    w = fake_client()
    apply_access.apply_uc_grants(w, "grp_genie_receivables", {("prod_recebiveis", "diamond")})
    kinds = [(c["securable_type"], c["full_name"]) for c in w.grants.calls]
    assert ("catalog", "prod_recebiveis") in kinds
    assert ("schema", "prod_recebiveis.diamond") in kinds
    schema_call = next(c for c in w.grants.calls if c["securable_type"] == "schema")
    assert schema_call["changes"] == [
        {"principal": "grp_genie_receivables", "add": ["USE_SCHEMA", "SELECT"]}]


def test_apply_uc_grants_passes_typed_permissions_change_not_raw_dicts():
    """Same class of bug as groups.patch: grants.update calls `.as_dict()` on each change before
    serializing, so every change passed here must be a typed `catalog.PermissionsChange`."""
    captured_changes: list = []
    w = fake_client()
    real_update = w.grants.update

    def spy_update(*, securable_type, full_name, changes):
        captured_changes.extend(changes)
        return real_update(securable_type=securable_type, full_name=full_name, changes=changes)

    w.grants.update = spy_update
    apply_access.apply_uc_grants(w, "grp_genie_receivables", {("prod_recebiveis", "diamond")})
    assert captured_changes
    for change in captured_changes:
        assert not isinstance(change, dict)
        assert hasattr(change, "as_dict"), f"expected a typed SDK model, got {type(change)}"


# --- Space permissions: per-principal, NO upgrade (SHOULD-FIX 3) ------------------------------

def test_apply_space_permissions_are_per_principal_no_upgrade():
    # SHOULD-FIX 3: mixed levels must NOT collapse to the group max — each principal keeps its own.
    w = fake_client()
    entries = [
        access_spec.SpacePermission(access_spec.Principal("alice@x"), "CAN_VIEW"),
        access_spec.SpacePermission(access_spec.Principal("bob@x"), "CAN_RUN"),
        access_spec.SpacePermission(access_spec.Principal("data_analysts", is_group=True), "CAN_VIEW"),
    ]
    apply_access.apply_space_permissions(w, "space-1", entries)
    assert len(w.permissions.calls) == 1
    acl = w.permissions.calls[0]["access_control_list"]
    by = {}
    for acr in acl:
        key = acr.user_name or acr.group_name
        by[key] = acr.permission_level
    assert by["alice@x"] == PermissionLevel.CAN_VIEW   # NOT upgraded to CAN_RUN
    assert by["bob@x"] == PermissionLevel.CAN_RUN
    assert by["data_analysts"] == PermissionLevel.CAN_VIEW
    # the group principal is applied via group_name, the users via user_name
    grp_acr = next(a for a in acl if a.group_name == "data_analysts")
    assert grp_acr.user_name is None


def test_apply_space_permissions_noop_when_no_entries():
    w = fake_client()
    apply_access.apply_space_permissions(w, "space-1", [])
    assert w.permissions.calls == []


# --- deployed Space id resolution by title (BLOCKER 2) ---------------------------------------

def test_resolve_space_id_matches_on_title():
    w = fake_client(spaces=[("sp-other", "Outro"), ("sp-1", "Recebíveis")])
    assert apply_access.resolve_space_id(w, "Recebíveis") == "sp-1"


def test_resolve_space_id_raises_when_not_found():
    w = fake_client(spaces=[("sp-1", "Recebíveis")])
    with pytest.raises(ValueError):
        apply_access.resolve_space_id(w, "Não Existe")


def test_resolve_space_id_raises_on_empty_title():
    w = fake_client(spaces=[("sp-1", "Recebíveis")])
    with pytest.raises(ValueError):
        apply_access.resolve_space_id(w, "")


# --- end-to-end main -------------------------------------------------------------------------

def _write_promo(tmp_path, spec, title_sidecar=None):
    rendered = tmp_path / "receivables.serialized_space.json"
    rendered.write_text(json.dumps(SPACE))
    sidecar = tmp_path / "receivables.access.json"
    sidecar.write_text(json.dumps(spec.to_dict()))
    return rendered, sidecar


def test_main_end_to_end_is_idempotent_on_rerun(tmp_path, monkeypatch):
    spec = access_spec.AccessSpec(
        space_permissions=(access_spec.SpacePermission(access_spec.Principal("ana@x.com")),),
        uc_principals=(access_spec.Principal("ana@x.com"),),
    )
    rendered, sidecar = _write_promo(tmp_path, spec)
    w = fake_client(spaces=[("space-1", "Recebíveis dev")])
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    # arg 3 is now the deployed Space TITLE (id resolved via genie.list_spaces)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(sidecar), "Recebíveis dev"])
    assert apply_access.main() == 0
    assert apply_access.main() == 0  # re-run: safe no-op
    assert w.groups.create_calls == 1
    group = w.groups.groups["grp_genie_receivables"]
    assert [m.value for m in group.members] == ["uid-ana@x.com"]  # id, not duplicated
    assert len(w.permissions.calls) == 2  # per-principal ACL re-applied both runs (idempotent)
    assert w.permissions.calls[0]["request_object_id"] == "space-1"  # resolved from title


def test_main_uc_only_does_not_resolve_space_id(tmp_path, monkeypatch):
    # A UC-only AccessSpec never touches the Space ACL -> must not need/resolve a Space id (a bad
    # title is irrelevant), but still applies group membership + UC grants.
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    rendered, sidecar = _write_promo(tmp_path, spec)
    w = fake_client(spaces=[])  # no spaces -> resolve_space_id would raise if called
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(sidecar), "irrelevant"])
    assert apply_access.main() == 0
    assert w.permissions.calls == []  # no Space ACL touched
    assert w.grants.calls  # UC grants applied
    assert [m.value for m in w.groups.groups["grp_genie_receivables"].members] == ["uid-ana@x.com"]


def test_main_fails_loud_when_space_id_unresolvable(tmp_path, monkeypatch):
    # BLOCKER 2: if Space permissions are declared but the deployed Space can't be found by title,
    # apply_access must RAISE (fail the deploy step), never warn-and-skip.
    spec = access_spec.AccessSpec(
        space_permissions=(access_spec.SpacePermission(access_spec.Principal("ana@x.com")),))
    rendered, sidecar = _write_promo(tmp_path, spec)
    w = fake_client(spaces=[("sp-1", "Some Other Title")])
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(sidecar), "Recebíveis dev"])
    with pytest.raises(ValueError):
        apply_access.main()


def test_main_skips_cleanly_when_no_sidecar(tmp_path, monkeypatch):
    rendered = tmp_path / "receivables.serialized_space.json"
    rendered.write_text(json.dumps(SPACE))
    w = fake_client()
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(tmp_path / "nope.access.json"), "t"])
    assert apply_access.main() == 0
    assert w.groups.create_calls == 0  # nothing declared -> nothing touched

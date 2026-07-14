"""Unit tests for scripts/apply_access.py — the GOVERNED (CI-run, prod-SP) enforcement of an
AccessSpec (F2). Fakes stand in for the SDK (`w.groups`, `w.grants`, `w.permissions`, `w.genie`) so
the direct-to-principal grant/permission-apply/title->id logic is unit-tested with no live
workspace.

DESIGN (changed 2026-07-14, live prod failure): access is applied DIRECTLY to each declared
principal, never via an intermediate group — `w.groups.create` in a workspace context creates a
WORKSPACE-LOCAL group, and UC grants only accept ACCOUNT-level principals (proven live). These
fakes model that: `FakeGroups` exists only so a declared GROUP's `meta.resourceType` can be looked
up (account vs workspace-local) to decide UC-grantability, and a deployed Space id is resolved via
genie.list_spaces() (not bundle summary).
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
    """SCIM group directory — read-only lookup by displayName, used only to resolve a declared
    GROUP principal's `meta.resourceType` (account `"Group"` vs workspace-local `"WorkspaceGroup"`)
    for the UC-grantability check. No create/patch here (apply_access no longer creates a group)."""

    def __init__(self, groups=None):
        # name -> resource_type ("Group" | "WorkspaceGroup" | None-if-unlisted-but-still-created)
        self._groups: dict[str, "str | None"] = dict(groups or {})

    def list(self, filter: str):  # noqa: A002 - mirrors the SDK kwarg
        name = filter.split('"')[1]  # `displayName eq "<name>"`
        if name not in self._groups:
            return []
        return [NS(id=f"gid-{name}", display_name=name,
                   meta=NS(resource_type=self._groups[name]))]


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


def fake_client(*, groups=None, spaces=None):
    return NS(groups=FakeGroups(groups), grants=FakeGrants(), permissions=FakePermissions(),
              genie=FakeGenie(spaces or []))


SPACE = {"data_sources": {"tables": [
    {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
    {"identifier": "prod_recebiveis.diamond.dim_arranjo"},
]}}


# --- UC grants: direct-to-principal (the design change) -------------------------------------

def test_apply_uc_grants_is_schema_scoped_additive_and_direct_per_principal():
    w = fake_client()
    apply_access.apply_uc_grants(w, [access_spec.Principal("ana@x.com")],
                                 {("prod_recebiveis", "diamond")})
    kinds = [(c["securable_type"], c["full_name"]) for c in w.grants.calls]
    assert ("catalog", "prod_recebiveis") in kinds
    assert ("schema", "prod_recebiveis.diamond") in kinds
    schema_call = next(c for c in w.grants.calls if c["securable_type"] == "schema")
    assert schema_call["changes"] == [{"principal": "ana@x.com", "add": ["USE_SCHEMA", "SELECT"]}]


def test_apply_uc_grants_grants_every_declared_principal_directly_no_group():
    w = fake_client()
    apply_access.apply_uc_grants(
        w, [access_spec.Principal("ana@x.com"), access_spec.Principal("bob@x.com")],
        {("prod_recebiveis", "diamond")})
    principals_granted = {c["changes"][0]["principal"] for c in w.grants.calls
                          if c["securable_type"] == "schema"}
    assert principals_granted == {"ana@x.com", "bob@x.com"}
    assert w.groups.list('displayName eq "anything"') == []  # never consulted for a user


def test_apply_uc_grants_grants_an_account_level_group_directly():
    w = fake_client(groups={"data_analysts": "Group"})  # account-level
    apply_access.apply_uc_grants(w, [access_spec.Principal("data_analysts", is_group=True)],
                                 {("prod_recebiveis", "diamond")})
    schema_call = next(c for c in w.grants.calls if c["securable_type"] == "schema")
    assert schema_call["changes"] == [{"principal": "data_analysts", "add": ["USE_SCHEMA", "SELECT"]}]


def test_apply_uc_grants_skips_a_workspace_local_group_with_a_loud_warning_never_crashes(capsys):
    w = fake_client(groups={"grp_genie_receivables": "WorkspaceGroup"})
    apply_access.apply_uc_grants(w, [access_spec.Principal("grp_genie_receivables", is_group=True)],
                                 {("prod_recebiveis", "diamond")})
    assert w.grants.calls == []  # never even attempted
    out = capsys.readouterr().out
    assert "::warning" in out and "grp_genie_receivables" in out


def test_apply_uc_grants_skipped_group_does_not_block_other_declared_principals():
    w = fake_client(groups={"grp_genie_receivables": "WorkspaceGroup"})
    apply_access.apply_uc_grants(
        w, [access_spec.Principal("grp_genie_receivables", is_group=True),
            access_spec.Principal("ana@x.com")],
        {("prod_recebiveis", "diamond")})
    principals_granted = {c["changes"][0]["principal"] for c in w.grants.calls
                          if c["securable_type"] == "schema"}
    assert principals_granted == {"ana@x.com"}


def test_apply_uc_grants_noop_when_every_declared_principal_is_non_grantable():
    w = fake_client(groups={"users": None})  # unverifiable -> falls back to the built-in-name check
    apply_access.apply_uc_grants(w, [access_spec.Principal("users", is_group=True)],
                                 {("prod_recebiveis", "diamond")})
    assert w.grants.calls == []


def test_apply_uc_grants_passes_typed_permissions_change_not_raw_dicts():
    """Same class of bug groups.patch used to hit: grants.update calls `.as_dict()` on each change
    before serializing, so every change passed here must be a typed `catalog.PermissionsChange`."""
    captured_changes: list = []
    w = fake_client()
    real_update = w.grants.update

    def spy_update(*, securable_type, full_name, changes):
        captured_changes.extend(changes)
        return real_update(securable_type=securable_type, full_name=full_name, changes=changes)

    w.grants.update = spy_update
    apply_access.apply_uc_grants(w, [access_spec.Principal("ana@x.com")], {("prod_recebiveis", "diamond")})
    assert captured_changes
    for change in captured_changes:
        assert not isinstance(change, dict)
        assert hasattr(change, "as_dict"), f"expected a typed SDK model, got {type(change)}"


# --- Space permissions: per-principal, NO upgrade (unchanged by the design change) -----------

def test_apply_space_permissions_are_per_principal_no_upgrade():
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


# --- deployed Space id resolution by title (fail loud on not-found AND on ambiguous match) ----

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


def test_resolve_space_id_fails_loud_on_a_duplicate_title_never_guesses():
    # Found live: two deployed Spaces sharing a title made the old first-match behavior pick the
    # WRONG one. Must raise, naming every candidate id, rather than silently returning the first.
    w = fake_client(spaces=[("sp-1", "Recebíveis"), ("sp-2", "Recebíveis")])
    with pytest.raises(ValueError, match="sp-1"):
        apply_access.resolve_space_id(w, "Recebíveis")


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
    # granted directly to the declared user, no intermediate group ever created
    granted = {c["changes"][0]["principal"] for c in w.grants.calls if c["securable_type"] == "schema"}
    assert granted == {"ana@x.com"}
    assert len(w.permissions.calls) == 2  # per-principal ACL re-applied both runs (idempotent)
    assert w.permissions.calls[0]["request_object_id"] == "space-1"  # resolved from title


def test_main_uc_only_does_not_resolve_space_id(tmp_path, monkeypatch):
    # A UC-only AccessSpec never touches the Space ACL -> must not need/resolve a Space id (a bad
    # title is irrelevant), but still applies UC grants directly to the declared principal.
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    rendered, sidecar = _write_promo(tmp_path, spec)
    w = fake_client(spaces=[])  # no spaces -> resolve_space_id would raise if called
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(sidecar), "irrelevant"])
    assert apply_access.main() == 0
    assert w.permissions.calls == []  # no Space ACL touched
    granted = {c["changes"][0]["principal"] for c in w.grants.calls if c["securable_type"] == "schema"}
    assert granted == {"ana@x.com"}


def test_main_fails_loud_when_space_id_unresolvable(tmp_path, monkeypatch):
    # If Space permissions are declared but the deployed Space can't be found by title, apply_access
    # must RAISE (fail the deploy step), never warn-and-skip.
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
    assert w.grants.calls == []  # nothing declared -> nothing touched


def test_main_workspace_local_group_declared_alongside_a_user_still_grants_the_user(tmp_path, monkeypatch):
    # A promotion declaring BOTH a workspace-local group (skipped, warned) and an individual user
    # (always grantable) must still grant the user — one non-grantable principal never blocks another.
    spec = access_spec.AccessSpec(
        uc_principals=(access_spec.Principal("grp_genie_receivables", is_group=True),
                      access_spec.Principal("ana@x.com")))
    rendered, sidecar = _write_promo(tmp_path, spec)
    w = fake_client(groups={"grp_genie_receivables": "WorkspaceGroup"}, spaces=[])
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(sidecar), "irrelevant"])
    assert apply_access.main() == 0
    granted = {c["changes"][0]["principal"] for c in w.grants.calls if c["securable_type"] == "schema"}
    assert granted == {"ana@x.com"}


def test_run_emits_error_annotation_on_failure(monkeypatch, capsys):
    """The CLI wrapper turns ANY failure into a ::error annotation (found live: the app's
    deploy-detail panel only saw GitHub's generic exit-code line for this step's errors)."""
    import apply_access

    def boom() -> int:
        raise RuntimeError("User does not have MANAGE on Catalog 'prod_recebiveis'")

    monkeypatch.setattr(apply_access, "main", boom)
    try:
        apply_access._run()
        raise AssertionError("should have re-raised")
    except RuntimeError:
        pass
    out = capsys.readouterr().out
    assert "::error title=apply-access::" in out
    assert "MANAGE on Catalog" in out

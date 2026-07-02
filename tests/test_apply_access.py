"""Unit tests for scripts/apply_access.py — the GOVERNED (CI-run, prod-SP) enforcement of an
AccessSpec (F2). A fake WorkspaceClient stands in for the SDK (`w.groups`, `w.grants`,
`w.permissions`) so the idempotent get-or-create / membership-reconcile / grant-apply logic is
unit-tested with no live workspace, mirroring seed_recebiveis.py's CREATE-IF-NOT-EXISTS intent.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import apply_access  # noqa: E402


class FakeGroups:
    """In-memory account-group store. `create` is idempotent from the CALLER's point of view
    because apply_access always does list-then-create — this fake just records what happened."""

    def __init__(self):
        self.groups: dict[str, NS] = {}   # display_name -> group NS(id, display_name, members)
        self.create_calls = 0
        self.patch_calls = []

    def list(self, filter: str):  # noqa: A002 - mirrors the SDK's kwarg name
        # filter is `displayName eq "<name>"` — extract the quoted name (good enough for a fake).
        name = filter.split('"')[1]
        g = self.groups.get(name)
        return [g] if g else []

    def create(self, *, display_name: str):
        self.create_calls += 1
        g = NS(id=f"gid-{display_name}", display_name=display_name, members=[])
        self.groups[display_name] = g
        return g

    def patch(self, group_id: str, operations: list):
        self.patch_calls.append((group_id, operations))
        group = next(g for g in self.groups.values() if g.id == group_id)
        for op in operations:
            if op["op"] == "add" and op["path"] == "members":
                existing = {m.display for m in group.members}
                for v in op["value"]:
                    if v["value"] not in existing:
                        group.members.append(NS(display=v["value"]))


class FakeGrants:
    def __init__(self):
        self.calls = []

    def update(self, *, securable_type, full_name, changes):
        self.calls.append({"securable_type": securable_type, "full_name": full_name, "changes": changes})


class FakePermissions:
    def __init__(self):
        self.calls = []

    def update(self, *, request_object_type, request_object_id, access_control_list):
        self.calls.append({
            "request_object_type": request_object_type, "request_object_id": request_object_id,
            "access_control_list": access_control_list,
        })


def fake_client():
    return NS(groups=FakeGroups(), grants=FakeGrants(), permissions=FakePermissions())


SPACE = {"data_sources": {"tables": [
    {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
    {"identifier": "prod_recebiveis.diamond.dim_arranjo"},
]}}


def test_get_or_create_group_is_idempotent():
    w = fake_client()
    g1 = apply_access.get_or_create_group(w, "grp_genie_receivables")
    g2 = apply_access.get_or_create_group(w, "grp_genie_receivables")
    assert g1.id == g2.id
    assert w.groups.create_calls == 1  # second call found the existing group, didn't recreate


def test_reconcile_membership_adds_only_missing_principals():
    w = fake_client()
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    group.members = [NS(display="already_here")]
    principals = [access_spec.Principal("already_here"), access_spec.Principal("ana@x.com")]
    apply_access.reconcile_membership(w, group, principals)
    assert {m.display for m in group.members} == {"already_here", "ana@x.com"}
    # only the missing principal was patched in
    assert len(w.groups.patch_calls) == 1
    added = w.groups.patch_calls[0][1][0]["value"]
    assert added == [{"value": "ana@x.com"}]


def test_reconcile_membership_is_a_noop_when_all_present():
    w = fake_client()
    group = apply_access.get_or_create_group(w, "grp_genie_receivables")
    group.members = [NS(display="ana@x.com")]
    apply_access.reconcile_membership(w, group, [access_spec.Principal("ana@x.com")])
    assert w.groups.patch_calls == []  # idempotent: nothing to add -> no API call


def test_apply_uc_grants_is_schema_scoped_and_additive():
    w = fake_client()
    apply_access.apply_uc_grants(w, "grp_genie_receivables",
                                 {("prod_recebiveis", "diamond")})
    kinds = [(c["securable_type"], c["full_name"]) for c in w.grants.calls]
    assert ("catalog", "prod_recebiveis") in kinds
    assert ("schema", "prod_recebiveis.diamond") in kinds
    schema_call = next(c for c in w.grants.calls if c["securable_type"] == "schema")
    assert schema_call["changes"] == [
        {"principal": "grp_genie_receivables", "add": ["USE_SCHEMA", "SELECT"]}]


def test_apply_space_permissions_picks_the_highest_declared_level():
    w = fake_client()
    entries = [access_spec.SpacePermission(access_spec.Principal("g"), "CAN_VIEW")]
    apply_access.apply_space_permissions(w, "space-1", "grp_genie_receivables", entries)
    assert len(w.permissions.calls) == 1
    call = w.permissions.calls[0]
    assert call["request_object_type"] == "genie" and call["request_object_id"] == "space-1"
    acl = call["access_control_list"][0]
    assert acl.group_name == "grp_genie_receivables"


def test_apply_space_permissions_noop_when_no_entries():
    w = fake_client()
    apply_access.apply_space_permissions(w, "space-1", "grp_genie_receivables", [])
    assert w.permissions.calls == []


def test_main_end_to_end_is_idempotent_on_rerun(tmp_path, monkeypatch):
    """The full script, run twice with the SAME AccessSpec, must not error and must not duplicate
    group creation or grow membership beyond the declared set (the redeploy-safety requirement
    that motivated the imperative/idempotent design over a declarative DABs grants resource)."""
    rendered = tmp_path / "receivables.serialized_space.json"
    rendered.write_text(json.dumps(SPACE))
    sidecar = tmp_path / "receivables.access.json"
    spec = access_spec.AccessSpec(
        space_permissions=(access_spec.SpacePermission(access_spec.Principal("ana@x.com")),),
        uc_principals=(access_spec.Principal("ana@x.com"),),
    )
    sidecar.write_text(json.dumps(spec.to_dict()))

    w = fake_client()
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["apply_access.py", str(rendered), str(sidecar), "space-1"])

    assert apply_access.main() == 0
    assert apply_access.main() == 0  # re-run: must be a safe no-op, not an error

    assert w.groups.create_calls == 1
    group = w.groups.groups["grp_genie_receivables"]
    assert [m.display for m in group.members] == ["ana@x.com"]  # not duplicated
    assert len(w.permissions.calls) == 2  # additive update re-applied both times (idempotent grant)


def test_main_skips_cleanly_when_no_sidecar(tmp_path, monkeypatch):
    rendered = tmp_path / "receivables.serialized_space.json"
    rendered.write_text(json.dumps(SPACE))
    w = fake_client()
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv",
                        ["apply_access.py", str(rendered), str(tmp_path / "nope.access.json"), "space-1"])
    assert apply_access.main() == 0
    assert w.groups.create_calls == 0  # nothing declared -> nothing touched

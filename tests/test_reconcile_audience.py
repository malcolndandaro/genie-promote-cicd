import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import audience_spec  # noqa: E402
import reconcile_audience  # noqa: E402

from databricks.sdk.service.iam import AccessControlResponse, ObjectPermissions, Permission, PermissionLevel


def _entry(name, level, *, group=False, inherited=False):
    return AccessControlResponse(
        group_name=name if group else None,
        user_name=None if group else name,
        all_permissions=[Permission(permission_level=PermissionLevel(level), inherited=inherited)],
    )


class FakePermissions:
    def __init__(self, entries):
        self.entries = list(entries)
        self.set_calls = []

    def get(self, request_object_type, request_object_id):
        assert request_object_type == "genie"
        return ObjectPermissions(access_control_list=list(self.entries))

    def set(self, request_object_type, request_object_id, *, access_control_list):
        self.set_calls.append(access_control_list)
        self.entries = [AccessControlResponse(
            group_name=request.group_name,
            user_name=request.user_name,
            service_principal_name=request.service_principal_name,
            all_permissions=[Permission(permission_level=request.permission_level, inherited=False)],
        ) for request in access_control_list]
        return ObjectPermissions(access_control_list=list(self.entries))


def test_desired_acl_removes_only_previous_can_run_and_preserves_stronger_unrelated_entries():
    previous = audience_spec.AudienceSpec((
        audience_spec.AudiencePrincipal("remove@example.com"),
        audience_spec.AudiencePrincipal("elevated@example.com"),
    ))
    desired = audience_spec.AudienceSpec((
        audience_spec.AudiencePrincipal("new-group", is_group=True),
        audience_spec.AudiencePrincipal("upgrade@example.com"),
    ))
    current = [
        _entry("remove@example.com", "CAN_RUN"),
        _entry("elevated@example.com", "CAN_MANAGE"),
        _entry("owner@example.com", "IS_OWNER"),
        _entry("app-sp", "CAN_MANAGE"),
        _entry("upgrade@example.com", "CAN_VIEW"),
    ]
    acl = reconcile_audience.desired_acl(current, desired, previous)
    by_name = {(entry.user_name or entry.group_name): entry.permission_level.value for entry in acl}
    assert "remove@example.com" not in by_name
    assert by_name["elevated@example.com"] == "CAN_MANAGE"
    assert by_name["owner@example.com"] == "IS_OWNER"
    assert by_name["app-sp"] == "CAN_MANAGE"
    assert by_name["upgrade@example.com"] == "CAN_RUN"
    assert by_name["new-group"] == "CAN_RUN"
    assert all(hasattr(entry, "as_dict") for entry in acl)


def test_reconcile_sets_typed_complete_acl_and_verifies_readback():
    desired = audience_spec.AudienceSpec((audience_spec.AudiencePrincipal("finance-users", True),))
    permissions = FakePermissions([_entry("owner@example.com", "IS_OWNER")])
    result = reconcile_audience.reconcile(NS(permissions=permissions), "space-1", desired)
    assert result == {"space_id": "space-1", "principals": ["finance-users"], "verified": True}
    assert len(permissions.set_calls) == 1
    assert all(hasattr(entry, "as_dict") for entry in permissions.set_calls[0])
    names = {entry.user_name or entry.group_name for entry in permissions.entries}
    assert names == {"owner@example.com", "finance-users"}


def test_inherited_only_acl_is_not_replayed_as_a_direct_grant():
    desired = audience_spec.AudienceSpec((audience_spec.AudiencePrincipal("finance-users", True),))
    acl = reconcile_audience.desired_acl(
        [_entry("inherited@example.com", "CAN_RUN", inherited=True)], desired)
    assert {entry.user_name or entry.group_name for entry in acl} == {"finance-users"}

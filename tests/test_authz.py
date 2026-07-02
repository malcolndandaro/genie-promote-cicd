"""Unit tests for app/authz.py — the A2 load-bearing authorization control.

No live workspace is ever contacted: `verify_identity` and `assert_can_access` both take a
WorkspaceClient-shaped object, so every SDK response (and every SDK failure) is a fake/injected
double. This mirrors the tests/test_app_logic.py injected-client convention.
"""
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import authz  # noqa: E402
from databricks.sdk.errors import NotFound, PermissionDenied  # noqa: E402


# --- verify_identity: the ONLY legitimate token -> identity resolution ------------------------


def test_verify_identity_resolves_user_name_and_groups(monkeypatch):
    fake_me = NS(user_name="ana@x", groups=[NS(display="account-users"), NS(display="genie-authors")])
    monkeypatch.setattr(authz, "WorkspaceClient",
                        lambda **kw: NS(current_user=NS(me=lambda: fake_me)))
    identity = authz.verify_identity("tok-ana", host="https://dev.example")
    assert identity.user_name == "ana@x"
    assert identity.group_names == frozenset({"account-users", "genie-authors"})


def test_verify_identity_tolerates_missing_groups(monkeypatch):
    fake_me = NS(user_name="ana@x", groups=None)
    monkeypatch.setattr(authz, "WorkspaceClient",
                        lambda **kw: NS(current_user=NS(me=lambda: fake_me)))
    identity = authz.verify_identity("tok-ana")
    assert identity.group_names == frozenset()


def test_verify_identity_fails_closed_on_sdk_error(monkeypatch):
    def boom(**kw):
        raise PermissionDenied("token rejected")

    monkeypatch.setattr(authz, "WorkspaceClient", boom)
    try:
        authz.verify_identity("bad-token")
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_verify_identity_fails_closed_on_unexpected_exception(monkeypatch):
    # Not every failure mode is a DatabricksError (e.g. a raw network timeout) — must still deny.
    def boom(**kw):
        raise TimeoutError("workspace unreachable")

    monkeypatch.setattr(authz, "WorkspaceClient", boom)
    try:
        authz.verify_identity("some-token")
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_verify_identity_fails_closed_on_empty_user_name(monkeypatch):
    fake_me = NS(user_name=None, groups=None)
    monkeypatch.setattr(authz, "WorkspaceClient",
                        lambda **kw: NS(current_user=NS(me=lambda: fake_me)))
    try:
        authz.verify_identity("tok")
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_verify_identity_uses_pat_auth_with_the_given_token(monkeypatch):
    seen = {}

    def fake_wc(**kw):
        seen.update(kw)
        return NS(current_user=NS(me=lambda: NS(user_name="ana@x", groups=None)))

    monkeypatch.setattr(authz, "WorkspaceClient", fake_wc)
    authz.verify_identity("tok-123", host="https://dev.example")
    assert seen == {"host": "https://dev.example", "token": "tok-123", "auth_type": "pat"}


# --- assert_can_access: live, fail-closed, per-action ACL check ----------------------------


def _acl_transport(entries):
    """A fake dev-sp transport whose `permissions.get` returns a fixed ACL, shaped like the real
    `ObjectPermissions`/`AccessControlResponse` (verified live against dev during A2 — see
    authz.py's module docstring)."""
    perms = NS(access_control_list=[
        NS(user_name=e.get("user_name"), group_name=e.get("group_name"),
           service_principal_name=e.get("service_principal_name"),
           all_permissions=[NS(permission_level=lvl) for lvl in e.get("levels", [])])
        for e in entries
    ])
    return NS(permissions=NS(get=lambda request_object_type, request_object_id: perms))


def test_assert_can_access_allows_direct_user_grant():
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
    transport = _acl_transport([{"user_name": "ana@x", "levels": ["CAN_MANAGE"]}])
    authz.assert_can_access(identity, "space-1", transport=transport)  # must not raise


def test_assert_can_access_allows_via_group_membership():
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset({"genie-authors"}))
    transport = _acl_transport([{"group_name": "genie-authors", "levels": ["CAN_EDIT"]}])
    authz.assert_can_access(identity, "space-1", transport=transport)  # must not raise


def test_assert_can_access_denies_a_requester_who_does_not_own_the_space():
    # THE core acceptance criterion: the SP (transport) can reach the space, but the ACL only
    # grants a DIFFERENT user — the guard must deny even though the transport call succeeds.
    identity = authz.VerifiedIdentity(user_name="mallory@x", group_names=frozenset())
    transport = _acl_transport([{"user_name": "ana@x", "levels": ["CAN_MANAGE"]}])
    try:
        authz.assert_can_access(identity, "space-1", transport=transport)
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_assert_can_access_denies_when_acl_entry_has_no_access_level():
    # A principal is listed in the ACL, but with no permission level that counts as "access"
    # (defensive: an empty/foreign permission set must not accidentally grant).
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
    transport = _acl_transport([{"user_name": "ana@x", "levels": []}])
    try:
        authz.assert_can_access(identity, "space-1", transport=transport)
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_assert_can_access_fails_closed_on_acl_check_error():
    # Explicit fail-closed test: the ACL read itself errors (space not found / dev SP unreachable /
    # dev mid-wipe / transient failure) -> MUST deny, never treat as "no restriction" or retry-open.
    def boom(request_object_type, request_object_id):
        raise NotFound("space not found")

    transport = NS(permissions=NS(get=boom))
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
    try:
        authz.assert_can_access(identity, "space-1", transport=transport)
        assert False, "expected AccessDenied (fail-closed)"
    except authz.AccessDenied:
        pass


def test_assert_can_access_fails_closed_on_unexpected_exception():
    # Even a non-DatabricksError failure (e.g. a network timeout surfacing as a bare exception)
    # must still deny — "unknown failure mode" is not an excuse to fail open.
    def boom(request_object_type, request_object_id):
        raise TimeoutError("dev workspace unreachable")

    transport = NS(permissions=NS(get=boom))
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
    try:
        authz.assert_can_access(identity, "space-1", transport=transport)
        assert False, "expected AccessDenied (fail-closed)"
    except authz.AccessDenied:
        pass


def test_assert_can_access_denies_on_empty_acl():
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
    transport = _acl_transport([])
    try:
        authz.assert_can_access(identity, "space-1", transport=transport)
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_assert_can_access_tolerates_enum_and_plain_string_permission_levels():
    # The SDK returns a PermissionLevel enum (.value == "CAN_MANAGE"); tolerate a plain string too
    # (defensive, mirrors _priv_name's convention elsewhere in the codebase).
    identity = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
    perms = NS(access_control_list=[
        NS(user_name="ana@x", group_name=None, service_principal_name=None,
           all_permissions=[NS(permission_level=NS(value="CAN_MANAGE"))]),
    ])
    transport = NS(permissions=NS(get=lambda request_object_type, request_object_id: perms))
    authz.assert_can_access(identity, "space-1", transport=transport)  # must not raise


# --- is_admin: verified-identity-only, never a raw header -----------------------------------


def test_is_admin_true_for_configured_admin():
    identity = authz.VerifiedIdentity(user_name="Admin@X", group_names=frozenset())
    assert authz.is_admin(identity, frozenset({"admin@x"})) is True


def test_is_admin_false_for_non_admin():
    identity = authz.VerifiedIdentity(user_name="rando@x", group_names=frozenset())
    assert authz.is_admin(identity, frozenset({"admin@x"})) is False


def test_is_admin_false_when_no_verified_identity():
    # None (no/invalid token) must never be treated as admin — fail closed.
    assert authz.is_admin(None, frozenset({"admin@x"})) is False

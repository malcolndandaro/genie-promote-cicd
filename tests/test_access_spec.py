"""Unit tests for AccessSpec (F2) — pure parse/serialize, no I/O.

Covers the acceptance criteria: BOTH permission systems modeled explicitly (neither silently
dropped), round-trips through JSON (the sidecar shape), and the per-Space group name is
deterministic + safe as an account group display name.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402


def test_empty_spec_round_trips_and_is_empty():
    spec = access_spec.AccessSpec()
    assert spec.is_empty()
    assert spec.all_principals() == ()
    d = spec.to_dict()
    assert d == {"space_permissions": [], "uc_principals": []}
    assert access_spec.AccessSpec.from_dict(d) == spec


def test_both_systems_modeled_independently_neither_dropped():
    """The core F2 shape guarantee: a principal declared ONLY for Space permissions and a
    DIFFERENT principal declared ONLY for UC grants both survive a round trip — neither half is
    silently collapsed into the other."""
    spec = access_spec.AccessSpec(
        space_permissions=(
            access_spec.SpacePermission(access_spec.Principal("business_users", is_group=True), "CAN_RUN"),
        ),
        uc_principals=(
            access_spec.Principal("etl_service_principal"),
        ),
    )
    assert not spec.is_empty()
    d = spec.to_dict()
    assert d["space_permissions"] == [
        {"principal": "business_users", "is_group": True, "level": "CAN_RUN"}]
    assert d["uc_principals"] == [{"principal": "etl_service_principal", "is_group": False}]

    back = access_spec.AccessSpec.from_dict(d)
    assert back == spec
    # neither system's principal leaks into the other
    assert [sp.principal.name for sp in back.space_permissions] == ["business_users"]
    assert [p.name for p in back.uc_principals] == ["etl_service_principal"]


def test_all_principals_is_the_deduped_union_of_both_systems():
    spec = access_spec.AccessSpec(
        space_permissions=(
            access_spec.SpacePermission(access_spec.Principal("data_analysts", is_group=True)),
        ),
        uc_principals=(
            access_spec.Principal("data_analysts", is_group=True),  # same principal, other system
            access_spec.Principal("ana@x.com"),
        ),
    )
    assert spec.all_principals() == ("data_analysts", "ana@x.com")


def test_invalid_space_permission_level_rejected():
    import pytest

    with pytest.raises(ValueError):
        access_spec.SpacePermission(access_spec.Principal("x"), level="CAN_MANAGE")


def test_from_dict_tolerates_missing_keys_and_blank_principal():
    spec = access_spec.AccessSpec.from_dict({
        "space_permissions": [{"principal": ""}, {"principal": "ok_group", "is_group": True}],
        "uc_principals": None,
    })
    # a blank principal name is dropped, not carried as a bogus empty-string grant target
    assert [sp.principal.name for sp in spec.space_permissions] == ["ok_group"]
    assert spec.uc_principals == ()


def test_from_dict_none_is_the_empty_spec():
    assert access_spec.AccessSpec.from_dict(None) == access_spec.AccessSpec()


def test_declared_groups_is_deduped_union_of_group_principals_only():
    """G9: `declared_groups()` is the class UC-grantability needs to be verified for — every
    declared GROUP across both systems, deduped, but NEVER an individual user (always
    account-level)."""
    spec = access_spec.AccessSpec(
        space_permissions=(
            access_spec.SpacePermission(access_spec.Principal("business_users", is_group=True)),
            access_spec.SpacePermission(access_spec.Principal("ana@x.com")),  # a user, not a group
        ),
        uc_principals=(
            access_spec.Principal("business_users", is_group=True),  # same group, other system
            access_spec.Principal("data_analysts", is_group=True),
            access_spec.Principal("etl_service_principal"),  # a user, not a group
        ),
    )
    assert spec.declared_groups() == ("business_users", "data_analysts")


def test_declared_groups_empty_when_only_users_declared():
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    assert spec.declared_groups() == ()


def test_group_name_for_is_deterministic_and_safe():
    assert access_spec.group_name_for("receivables") == "grp_genie_receivables"
    # same slug -> same group name every time (idempotent enforcement depends on this)
    assert access_spec.group_name_for("receivables") == access_spec.group_name_for("receivables")
    # unsafe characters are normalized (a group display name must be predictable/safe)
    assert access_spec.group_name_for("Some Space! #1") == "grp_genie_some_space_1"

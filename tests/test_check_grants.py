"""Unit tests for scripts/check_grants.py's sidecar-loading helper (F2). The rest of the script is
a thin CLI wrapper around a live WorkspaceClient (mirrors app_logic._effective_grants) so it isn't
otherwise unit-tested; `_declared_principals` is pure and is the F2-specific piece worth covering
in isolation from a live workspace.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import check_grants  # noqa: E402


def test_no_sidecar_falls_back_to_base_consumer_group_only(tmp_path):
    missing = tmp_path / "nope.access.json"
    assert check_grants._declared_principals(str(missing), "account users") == ["account users"]


def test_none_sidecar_path_falls_back_to_base_consumer_group_only():
    assert check_grants._declared_principals(None, "account users") == ["account users"]


def test_sidecar_principals_are_unioned_with_base_group_never_replace_it(tmp_path):
    spec = access_spec.AccessSpec(
        space_permissions=(access_spec.SpacePermission(access_spec.Principal("data_analysts", is_group=True)),),
        uc_principals=(access_spec.Principal("ana@x.com"),),
    )
    sidecar = tmp_path / "receivables.access.json"
    sidecar.write_text(json.dumps(spec.to_dict()))

    out = check_grants._declared_principals(str(sidecar), "account users")
    assert out == ["account users", "data_analysts", "ana@x.com"]  # base group first, never dropped

"""Compatibility entrypoint tests: AudienceSpec ACL only, never UC mutation."""
import inspect
import json
import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import apply_access  # noqa: E402
import audience_spec  # noqa: E402


class FakeGenie:
    def __init__(self, spaces):
        self._spaces = [NS(space_id=space_id, title=title) for space_id, title in spaces]

    def list_spaces(self):
        return NS(spaces=list(self._spaces))


def test_resolve_space_id_matches_exact_title_and_refuses_ambiguity():
    w = NS(genie=FakeGenie([("s1", "Recebíveis"), ("s2", "Outro")]))
    assert apply_access.resolve_space_id(w, "Recebíveis") == "s1"
    with pytest.raises(ValueError, match="no deployed"):
        apply_access.resolve_space_id(w, "Ausente")
    duplicate = NS(genie=FakeGenie([("s1", "Recebíveis"), ("s2", "Recebíveis")]))
    with pytest.raises(ValueError, match="refusing to guess"):
        apply_access.resolve_space_id(duplicate, "Recebíveis")


def test_load_audience_translates_legacy_can_run_and_discards_uc(tmp_path, capsys):
    path = tmp_path / "space.access.json"
    path.write_text(json.dumps({
        "space_permissions": [{"principal": "users", "is_group": True, "level": "CAN_RUN"}],
        "uc_principals": [{"principal": "ana@example.com", "is_group": False}],
    }))
    spec = apply_access.load_audience(str(path))
    assert spec.to_dict() == {"principals": [{"principal": "users", "is_group": True}]}
    assert "UC principal" in capsys.readouterr().out


def test_load_audience_rejects_legacy_can_view(tmp_path):
    path = tmp_path / "space.access.json"
    path.write_text(json.dumps({
        "space_permissions": [{"principal": "users", "level": "CAN_VIEW"}],
        "uc_principals": [],
    }))
    with pytest.raises(audience_spec.LegacyAudienceError, match="CAN_VIEW"):
        apply_access.load_audience(str(path))


def test_main_reconciles_canonical_audience_without_any_grants_api(tmp_path, monkeypatch):
    rendered = tmp_path / "space.serialized_space.json"
    rendered.write_text("{}")
    sidecar = tmp_path / "space.audience.json"
    sidecar.write_text(json.dumps({"principals": [{"principal": "finance-users", "is_group": True}]}))
    w = NS(genie=FakeGenie([("space-1", "Recebíveis")]), permissions=NS())
    captured = {}
    monkeypatch.setattr(apply_access, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(apply_access.reconcile_audience, "reconcile",
                        lambda client, space_id, desired: captured.update(
                            client=client, space_id=space_id, desired=desired.to_dict()) or {
                            "principals": list(desired.names())})
    monkeypatch.setattr(sys, "argv", ["apply_access.py", str(rendered), str(sidecar), "Recebíveis"])
    assert apply_access.main() == 0
    assert captured == {"client": w, "space_id": "space-1",
                        "desired": {"principals": [{"principal": "finance-users", "is_group": True}]}}
    assert not hasattr(w, "grants")


def test_main_skips_missing_sidecar_without_constructing_client(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_access, "WorkspaceClient",
                        lambda: (_ for _ in ()).throw(AssertionError("must not construct client")))
    monkeypatch.setattr(sys, "argv", ["apply_access.py", "rendered", str(tmp_path / "missing"), "x"])
    assert apply_access.main() == 0


def test_compatibility_entrypoint_source_has_no_uc_grant_mutation():
    source = inspect.getsource(apply_access)
    assert "grants.update" not in source
    assert "Privilege.SELECT" not in source
    assert "apply_uc_grants" not in source


def test_run_emits_error_annotation_and_reraises(monkeypatch, capsys):
    monkeypatch.setattr(apply_access, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        apply_access._run()
    assert "::error title=apply-access::RuntimeError: boom" in capsys.readouterr().out

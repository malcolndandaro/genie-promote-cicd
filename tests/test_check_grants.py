"""Unit tests for scripts/check_grants.py's pure/injectable helpers (F2, G9). The rest of the
script is a thin CLI wrapper around a live WorkspaceClient (mirrors app_logic._effective_grants) so
it isn't otherwise unit-tested; `_load_access_spec` and the grantability helpers are what's worth
covering in isolation from a live workspace.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import check_grants  # noqa: E402


def test_no_sidecar_returns_the_empty_spec(tmp_path):
    missing = tmp_path / "nope.access.json"
    assert check_grants._load_access_spec(str(missing)) == access_spec.AccessSpec()


def test_none_sidecar_path_returns_the_empty_spec():
    assert check_grants._load_access_spec(None) == access_spec.AccessSpec()


def test_sidecar_is_loaded_as_its_own_access_spec_not_unioned_with_the_base_group(tmp_path):
    """G9: `_load_access_spec` returns the DECLARED principals on their own — `main` checks them
    SEPARATELY from the base consumer group now (declared-miss is advisory, baseline-miss is not),
    so this helper no longer prepends/unions the base group into a single flat list."""
    spec = access_spec.AccessSpec(
        space_permissions=(access_spec.SpacePermission(access_spec.Principal("data_analysts", is_group=True)),),
        uc_principals=(access_spec.Principal("ana@x.com"),),
    )
    sidecar = tmp_path / "receivables.access.json"
    sidecar.write_text(json.dumps(spec.to_dict()))

    loaded = check_grants._load_access_spec(str(sidecar))
    assert loaded == spec
    assert loaded.all_principals() == ("data_analysts", "ana@x.com")  # no base group in here


# --- G9: UC-grantability of a declared GROUP (Additional scope) --------------------------------


class _FakeGroups:
    """`list(filter=...)` -> the matching fake group(s), by `displayName`. Each entry is an
    `NS(display_name=..., meta=NS(resource_type=...))` — mirrors the SDK's `iam.Group`/`ResourceMeta`
    shape closely enough for `_group_resource_type` to read `.meta.resource_type` off it."""

    def __init__(self, groups):
        self._groups = groups

    def list(self, filter=None):  # noqa: A002 - mirrors the SDK kwarg
        name = filter.split('"')[1]
        return [g for g in self._groups if g.display_name == name]


def test_group_resource_type_reads_scim_meta():
    w = NS(groups=_FakeGroups([NS(display_name="grp_x", meta=NS(resource_type="WorkspaceGroup"))]))
    assert check_grants._group_resource_type(w, "grp_x") == "WorkspaceGroup"


def test_group_resource_type_none_when_group_not_found():
    w = NS(groups=_FakeGroups([]))
    assert check_grants._group_resource_type(w, "ghost_group") is None


def test_non_grantable_declared_flags_workspace_local_groups_only():
    w = NS(groups=_FakeGroups([
        NS(display_name="users", meta=NS(resource_type="WorkspaceGroup")),
        NS(display_name="data_analysts", meta=NS(resource_type="Group")),
    ]))
    spec = access_spec.AccessSpec(uc_principals=(
        access_spec.Principal("users", is_group=True),
        access_spec.Principal("data_analysts", is_group=True),
        access_spec.Principal("ana@x.com"),  # a user — never checked here
    ))
    assert check_grants._non_grantable_declared(w, spec) == ["users"]


# --- G9 (annotations): bare print()s never became GitHub check-run annotations, so the app's ----
# check-details fallback (github_app._summarize_annotations) had nothing of ours to show and
# surfaced GitHub's own generic noise instead. Real `::error::`/`::warning::` workflow commands fix
# that — GitHub auto-converts a step's own stdout lines in this form into annotations.


def test_gh_escape_encodes_percent_cr_and_lf_per_the_workflow_command_format():
    assert check_grants._gh_escape("100%") == "100%25"
    assert check_grants._gh_escape("a\r\nb") == "a%0D%0Ab"
    assert check_grants._gh_escape("linha 1\nlinha 2") == "linha 1%0Alinha 2"


def test_emit_annotation_prints_a_github_workflow_command(capsys):
    check_grants._emit_annotation("error", "'users' não tem SELECT em prod.diamond.t")
    out = capsys.readouterr().out
    assert out == "::error title=GRANT-01::'users' não tem SELECT em prod.diamond.t\n"


def test_emit_annotation_escapes_newlines_in_the_message(capsys):
    check_grants._emit_annotation("warning", "linha 1\nlinha 2")
    out = capsys.readouterr().out
    assert out == "::warning title=GRANT-01::linha 1%0Alinha 2\n"


# --- G9: main() end-to-end — the 4 baseline x declared combinations, mirroring the app's ---------


def _fake_grants_client(privileges_by_table, groups=()):
    """A fake WorkspaceClient covering both calls `main()` makes: `grants.get_effective` (real
    shape: privilege_assignments[].privileges[] each with a `.privilege` field) and `groups.list`
    (for the grantability lookup)."""
    def get_effective(securable_type, full_name):
        assert securable_type == "TABLE", f"bad securable_type: {securable_type!r}"
        privs = privileges_by_table.get(full_name, {})
        return NS(privilege_assignments=[
            NS(principal=p, privileges=[NS(privilege=v) for v in vs]) for p, vs in privs.items()
        ])
    return NS(grants=NS(get_effective=get_effective), groups=_FakeGroups(list(groups)))


def _write_rendered_space(tmp_path):
    rendered = tmp_path / "receivables.serialized_space.json"
    rendered.write_text(json.dumps({"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
    ]}}))
    return rendered


def _write_sidecar(tmp_path, spec):
    sidecar = tmp_path / "receivables.access.json"
    sidecar.write_text(json.dumps(spec.to_dict()))
    return sidecar


TABLE = "prod_recebiveis.diamond.fato_recebiveis"


def test_main_baseline_ok_declared_ok_exits_clean(tmp_path, monkeypatch, capsys):
    rendered = _write_rendered_space(tmp_path)
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    sidecar = _write_sidecar(tmp_path, spec)
    w = _fake_grants_client({TABLE: {"account users": ["SELECT"], "ana@x.com": ["SELECT"]}})
    monkeypatch.setattr(check_grants, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["check_grants.py", str(rendered), "account users", str(sidecar)])
    assert check_grants.main() == 0
    assert "🔴" not in capsys.readouterr().out


def test_main_baseline_miss_blocks_exit_1(tmp_path, monkeypatch, capsys):
    """Baseline (unchanged): a missing baseline grant still exits 1 — the pipeline never grants it."""
    rendered = _write_rendered_space(tmp_path)
    w = _fake_grants_client({TABLE: {}})  # nobody has SELECT
    monkeypatch.setattr(check_grants, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["check_grants.py", str(rendered), "account users"])
    out = check_grants.main()
    assert out == 1
    captured = capsys.readouterr().out
    assert "🔴" in captured
    # G9: a REAL GitHub annotation too — a bare print() never becomes one.
    assert "::error title=GRANT-01::" in captured and "account users" in captured


def test_main_declared_miss_is_advisory_exits_0(tmp_path, monkeypatch, capsys):
    """A declared principal missing SELECT is a warning, not a block — apply_access grants it at
    deploy. Baseline is fine, so main() exits 0."""
    rendered = _write_rendered_space(tmp_path)
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    sidecar = _write_sidecar(tmp_path, spec)
    w = _fake_grants_client({TABLE: {"account users": ["SELECT"]}})  # ana@x.com missing
    monkeypatch.setattr(check_grants, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["check_grants.py", str(rendered), "account users", str(sidecar)])
    assert check_grants.main() == 0
    captured = capsys.readouterr().out
    assert "🟡" in captured and "ana@x.com" in captured and "apply_access" in captured
    assert "🔴" not in captured
    # G9: emitted as a WARNING-level annotation (advisory), not an error.
    assert "::warning title=GRANT-01::" in captured and "ana@x.com" in captured
    assert "::error title=GRANT-01::" not in captured


def test_main_mixed_baseline_miss_and_declared_miss_still_blocks_and_distinguishes_both(
    tmp_path, monkeypatch, capsys,
):
    """Misto: baseline-miss + declared-miss together — main() still exits 1 (the baseline BLOCKER),
    and the printed output shows BOTH classes distinctly (advisory line + BLOCKER line)."""
    rendered = _write_rendered_space(tmp_path)
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    sidecar = _write_sidecar(tmp_path, spec)
    w = _fake_grants_client({TABLE: {}})  # neither the baseline group nor ana@x.com has SELECT
    monkeypatch.setattr(check_grants, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["check_grants.py", str(rendered), "account users", str(sidecar)])
    out = check_grants.main()
    assert out == 1
    captured = capsys.readouterr().out
    assert "🔴" in captured and "account users" in captured  # baseline: blocks
    assert "🟡" in captured and "ana@x.com" in captured  # declared: advisory, distinguished
    # G9: BOTH classes also emitted as real annotations, at the right level each.
    assert "::error title=GRANT-01::" in captured and "account users" in captured
    assert "::warning title=GRANT-01::" in captured and "ana@x.com" in captured


def test_main_declared_non_grantable_group_gets_the_not_grantable_advisory_and_exits_0(tmp_path, monkeypatch, capsys):
    rendered = _write_rendered_space(tmp_path)
    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("users", is_group=True),))
    sidecar = _write_sidecar(tmp_path, spec)
    w = _fake_grants_client(
        {TABLE: {"account users": ["SELECT"]}},
        groups=[NS(display_name="users", meta=NS(resource_type="WorkspaceGroup"))],
    )
    monkeypatch.setattr(check_grants, "WorkspaceClient", lambda: w)
    monkeypatch.setattr(sys, "argv", ["check_grants.py", str(rendered), "account users", str(sidecar)])
    assert check_grants.main() == 0
    captured = capsys.readouterr().out
    finding_line = next(line for line in captured.splitlines() if "grupo local do workspace" in line)
    assert "apply_access" not in finding_line  # THIS finding never promises a grant that can't happen

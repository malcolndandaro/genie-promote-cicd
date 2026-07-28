"""`render.sh`'s per-dashboard loop + the offline dashboard CI gates.

`render.sh` is shelled out into a tmp repo — the same technique `test_render_audience.py` uses — so
these assert the REAL script's behaviour, not a re-implementation of it. That matters here because the
generated YAML is a deploy contract: an entry in the wrong place, or a missing sidecar copy, breaks the
deploy rather than any test.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DASHBOARD = {
    "datasets": [{
        "name": "ds_volume",
        "displayName": "Volume",
        "queryLines": [
            "SELECT a.bandeira AS bandeira, SUM(f.valor) AS total\n",
            "FROM dev_recebiveis.diamond.fato_recebiveis AS f\n",
            "JOIN dev_recebiveis.diamond.dim_arranjo AS a ON f.arranjo = a.arranjo\n",
            "GROUP BY a.bandeira",
        ],
    }],
    "pages": [{
        "name": "p1",
        "displayName": "Painel",
        "layout": [
            {"widget": {"name": "w1", "spec": {"widgetType": "bar"},
                        "queries": [{"query": {"datasetName": "ds_volume",
                                                "fields": [{"name": "bandeira"}]}}]}},
            # Prose containing BOTH a URL and a dev catalog name — the two live-probed shapes that
            # break a naive whole-document catalog scan.
            {"widget": {"name": "md", "multilineTextboxSpec": {"lines": [
                "# dev_recebiveis.diamond.fato_recebiveis",
                "Ver [wiki](https://en.wikipedia.org/wiki/Dashboard)",
            ]}}},
        ],
    }],
}


def _repo(tmp_path: Path, *, title: str | None = "Painel de Recebíveis", audience=True,
          doc=None, slug="recebiveis") -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "dashboards").mkdir(parents=True)
    for script in ("render.sh", "pre_render.py"):
        shutil.copy2(ROOT / "scripts" / script, tmp_path / "scripts" / script)
    (tmp_path / "src" / "dashboards" / f"{slug}.lvdash.json").write_text(
        json.dumps(doc if doc is not None else DASHBOARD, ensure_ascii=False), encoding="utf-8")
    if title is not None:
        (tmp_path / "src" / "dashboards" / f"{slug}.title").write_text(title + "\n", encoding="utf-8")
    if audience:
        (tmp_path / "src" / "dashboards" / f"{slug}.audience.json").write_text(
            json.dumps({"principals": [{"principal": "users", "is_group": True}]}), encoding="utf-8")
    return tmp_path


def _render(tmp_path: Path, check=True):
    return subprocess.run(
        ["bash", "scripts/render.sh", "prod"], cwd=tmp_path,
        env={**os.environ, "FROM_ENV": "dev", "DOMAIN": "recebiveis"},
        text=True, capture_output=True, check=check)


def test_render_emits_one_dashboard_resource_per_slug_under_the_prod_target(tmp_path):
    """Prod-scoping is a correctness requirement, not tidiness: promoted content is prod-only, and an
    un-scoped entry would create prod dashboards on a `-t dev` deploy."""
    repo = _repo(tmp_path)
    _render(repo)
    generated = (repo / "build" / "resources.gen.yml").read_text(encoding="utf-8")

    assert "targets:" in generated and "  prod:" in generated
    # The dashboards block must be INSIDE targets.prod.resources (6-space indent), like genie_spaces.
    assert "      dashboards:\n" in generated
    assert "        recebiveis:\n" in generated
    # display_name comes from the .title sidecar; the warehouse stays a bundle variable.
    assert 'display_name: "Painel de Recebíveis"' in generated
    assert "warehouse_id: ${var.warehouse_id}" in generated
    # file_path is relative to the GENERATED file's dir (build/), not the repo root.
    assert "file_path: ./dashboards/recebiveis.lvdash.json" in generated
    assert "./build/dashboards" not in generated


def test_render_copies_the_sidecars_the_deploy_reads(tmp_path):
    """The deploy resolves the live id from `.title` and reconciles from `.audience.json`, both read
    out of build/ — so render must carry them forward."""
    repo = _repo(tmp_path)
    _render(repo)
    built = repo / "build" / "dashboards"

    assert (built / "recebiveis.title").read_text(encoding="utf-8").strip() == "Painel de Recebíveis"
    assert json.loads((built / "recebiveis.audience.json").read_text(encoding="utf-8")) == {
        "principals": [{"principal": "users", "is_group": True}]}


def test_render_rebinds_the_dashboard_to_the_target_catalog(tmp_path):
    repo = _repo(tmp_path)
    _render(repo)
    rendered = (repo / "build" / "dashboards" / "recebiveis.lvdash.json").read_text(encoding="utf-8")

    assert "prod_recebiveis.diamond.fato_recebiveis" in rendered
    assert "dev_recebiveis" not in rendered  # including inside the markdown prose


def test_render_does_not_block_on_a_markdown_url(tmp_path):
    """THE regression: scanned whole-document, `en.wikipedia.org` reads as catalog `en` and the strict
    allowlist fails a perfectly good dashboard. The dashboard scan looks only at dataset SQL."""
    repo = _repo(tmp_path)
    result = _render(repo, check=False)

    assert result.returncode == 0, result.stderr
    assert "dataset SQL" in result.stdout  # the narrowed scan actually ran


def test_render_still_blocks_a_foreign_catalog_inside_dataset_sql(tmp_path):
    """The narrowed scan must not become a smuggling route."""
    doc = json.loads(json.dumps(DASHBOARD))
    doc["datasets"][0]["queryLines"] = ["SELECT 1 FROM sbx_recebiveis.diamond.f"]
    repo = _repo(tmp_path, doc=doc)
    result = _render(repo, check=False)

    assert result.returncode != 0
    assert "sbx_recebiveis" in (result.stdout + result.stderr)


def test_render_fails_closed_without_a_title_sidecar(tmp_path):
    """`.title` becomes display_name AND is the deploy's only id-resolution key, so a missing one must
    fail at render — not leave a deployed dashboard nobody can resolve."""
    repo = _repo(tmp_path, title=None)
    result = _render(repo, check=False)

    assert result.returncode != 0
    assert "title" in (result.stdout + result.stderr)


def test_render_is_a_clean_noop_with_no_dashboard_content(tmp_path):
    """A standalone engine checkout (no content overlay) must still render + validate."""
    (tmp_path / "scripts").mkdir()
    for script in ("render.sh", "pre_render.py"):
        shutil.copy2(ROOT / "scripts" / script, tmp_path / "scripts" / script)
    _render(tmp_path)
    generated = (tmp_path / "build" / "resources.gen.yml").read_text(encoding="utf-8")

    assert "genie_spaces: {}" in generated
    # No dashboards key at all rather than an empty one — an empty mapping would be a desired state.
    assert "dashboards:" not in generated


def test_render_emits_two_dashboards_without_engine_changes(tmp_path):
    """The whole point of the loop: a second dashboard needs no engine edit."""
    repo = _repo(tmp_path)
    (repo / "src" / "dashboards" / "segundo.lvdash.json").write_text(
        json.dumps(DASHBOARD, ensure_ascii=False), encoding="utf-8")
    (repo / "src" / "dashboards" / "segundo.title").write_text("Segundo Painel\n", encoding="utf-8")
    (repo / "src" / "dashboards" / "segundo.audience.json").write_text(
        json.dumps({"principals": [{"principal": "users", "is_group": True}]}), encoding="utf-8")
    result = _render(repo)

    generated = (repo / "build" / "resources.gen.yml").read_text(encoding="utf-8")
    assert "        recebiveis:\n" in generated and "        segundo:\n" in generated
    assert "2 dashboard(s)" in result.stdout


# --- the offline structural CI gate ---------------------------------------------------------------


def _check_dashboard(path: Path, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_dashboard.py"), str(path), *args],
        cwd=ROOT, text=True, capture_output=True)


def test_check_dashboard_passes_a_sound_panel(tmp_path):
    doc = json.loads(json.dumps(DASHBOARD).replace("dev_recebiveis", "prod_recebiveis"))
    # Drop the markdown widget so the advisory DASH-04 (the URL) doesn't muddy the assertion.
    doc["pages"][0]["layout"] = doc["pages"][0]["layout"][:1]
    path = tmp_path / "d.lvdash.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = _check_dashboard(path)
    assert result.returncode == 0 and "OK" in result.stdout


def test_check_dashboard_blocks_a_dangling_dataset_with_an_annotation(tmp_path):
    doc = json.loads(json.dumps(DASHBOARD))
    doc["pages"][0]["layout"][0]["widget"]["queries"][0]["query"]["datasetName"] = "ds_gone"
    path = tmp_path / "d.lvdash.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = _check_dashboard(path)
    assert result.returncode == 1
    # A real GitHub annotation, so the app's check-details panel surfaces it.
    assert "::error title=DASH-01::" in result.stdout


def test_check_dashboard_reports_prose_catalogs_as_a_warning_not_an_error(tmp_path):
    """DASH-04 is advisory by design — prose is not a data path."""
    path = tmp_path / "d.lvdash.json"
    path.write_text(json.dumps(DASHBOARD), encoding="utf-8")

    result = _check_dashboard(path, "--to", "prod", "--domain", "recebiveis")
    assert result.returncode == 0
    assert "::warning title=DASH-04::" in result.stdout
    assert "::error" not in result.stdout


def test_check_dashboard_is_a_noop_for_a_missing_artifact(tmp_path):
    result = _check_dashboard(tmp_path / "absent.lvdash.json")
    assert result.returncode == 0


# --- the prod SQL gate: the parts testable without a workspace ------------------------------------


def _check_sql(path: Path, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_dashboard_sql.py"), str(path), *args],
        cwd=ROOT, text=True, capture_output=True,
        env={k: v for k, v in os.environ.items() if k != "DATABRICKS_WAREHOUSE_ID"})


def test_sql_gate_fails_closed_without_a_warehouse(tmp_path):
    """A gate that cannot run must never report success (AGENTS.md: workflows fail closed)."""
    path = tmp_path / "d.lvdash.json"
    path.write_text(json.dumps(DASHBOARD), encoding="utf-8")

    result = _check_sql(path)
    assert result.returncode == 2
    assert "::error title=DASH-SQL::" in result.stdout


def test_sql_gate_is_a_noop_for_a_missing_artifact(tmp_path):
    result = _check_sql(tmp_path / "absent.lvdash.json", "--warehouse-id", "wh1")
    assert result.returncode == 0


def test_sql_gate_detects_parameterized_queries():
    """Parameter detection decides skip-vs-evaluate, so it is worth pinning directly."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_dashboard_sql as gate

    assert gate._is_parameterized("SELECT * FROM t WHERE d >= :`Inicio`")
    assert gate._is_parameterized("SELECT * FROM t WHERE id = :id")
    # A cast is NOT a parameter — over-eager detection would silently skip real queries.
    assert not gate._is_parameterized("SELECT x::int FROM t")
    assert not gate._is_parameterized("SELECT a.b AS c FROM prod_x.y.z AS a")


def test_sql_gate_extracts_every_dataset_query():
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_dashboard_sql as gate

    doc = {"datasets": [
        {"name": "a", "queryLines": ["SELECT 1"]},
        {"name": "b", "query": "SELECT 2"},
        {"name": "empty", "queryLines": ["   "]},
    ]}
    assert [name for name, _sql in gate._datasets(doc)] == ["a", "b"]

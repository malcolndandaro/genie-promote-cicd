"""The AI/BI dashboard promotion flow — the kind seam, end to end, offline.

Hermetic by the autouse `conftest.py` fixture (no ambient Databricks auth, no network). Every SDK
interaction is a `SimpleNamespace` duck-typed fake, matching `test_app_logic.py`'s convention.

These tests pin the BEHAVIOURS that make a dashboard promotion trustworthy, and — just as important —
that adding it changed nothing for Genie Spaces.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import app_logic  # noqa: E402
import audience_check  # noqa: E402
import audience_spec  # noqa: E402
import authz  # noqa: E402
import dashboard_check  # noqa: E402
import pre_render  # noqa: E402
import resource_kind as rk  # noqa: E402
import workspace_resource as wr  # noqa: E402


def _audience():
    return audience_spec.AudienceSpec.from_dict({"principals": [{"principal": "users", "is_group": True}]})


# A minimal but structurally REAL dashboard: one dataset, one bar widget bound to it, one markdown
# widget. The markdown deliberately contains BOTH a URL and a dev catalog name in prose — the two
# shapes probed on a live dev dashboard that break a naive whole-document ref scan.
DASHBOARD = {
    "datasets": [{
        "name": "ds_volume",
        "displayName": "Volume por bandeira",
        "queryLines": [
            "SELECT a.bandeira AS bandeira, SUM(f.valor) AS volume_total\n",
            "FROM dev_recebiveis.diamond.fato_recebiveis AS f\n",
            "JOIN dev_recebiveis.diamond.dim_arranjo AS a ON f.arranjo = a.arranjo\n",
            "GROUP BY a.bandeira",
        ],
    }],
    "pages": [{
        "name": "p1",
        "displayName": "Painel Recebíveis",
        "layout": [
            {"widget": {"name": "w_bar", "spec": {"version": 3, "widgetType": "bar"},
                        "queries": [{"name": "q", "query": {
                            "datasetName": "ds_volume",
                            "fields": [{"name": "bandeira"}, {"name": "volume_total"}]}}]}},
            {"widget": {"name": "w_md", "multilineTextboxSpec": {"lines": [
                "# Painel de dev_recebiveis.diamond.fato_recebiveis",
                "Referência: [KS test](https://en.wikipedia.org/wiki/K-S_test)",
            ]}}},
        ],
    }],
    "uiSettings": {"theme": {}},
}


def _dashboard_client(*, dashboards=(("d1", "Painel Recebíveis"),), acl_levels=("CAN_MANAGE",),
                      serialized=None):
    """A fake client exposing the `lakeview` + `permissions` surfaces the adapter and guard use."""
    doc = json.dumps(serialized if serialized is not None else DASHBOARD, ensure_ascii=False)

    def _get(dashboard_id):
        return NS(dashboard_id=dashboard_id, display_name="Painel Recebíveis",
                  serialized_dashboard=doc, warehouse_id="wh1", lifecycle_state="ACTIVE")

    def _perms_get(request_object_type, request_object_id):
        assert request_object_type == "dashboards", "the dashboard guard must use the plural type"
        return NS(access_control_list=[NS(
            user_name="ana@x.com", group_name=None, service_principal_name=None,
            all_permissions=[NS(permission_level=lvl) for lvl in acl_levels])])

    return NS(
        lakeview=NS(
            list=lambda: [NS(dashboard_id=i, display_name=t, lifecycle_state="ACTIVE",
                             serialized_dashboard=None) for i, t in dashboards],
            get=_get,
        ),
        permissions=NS(get=_perms_get),
    )


# --- the registry: the kind seam itself -----------------------------------------------------------


def test_registry_exposes_the_probed_platform_facts_per_kind():
    """These four values were each verified against a live workspace; a regression here silently
    points the pipeline at the wrong API (ADR-0007: a generic example is not evidence)."""
    genie, dash = rk.GENIE_SPACE_KIND, rk.DASHBOARD_KIND
    assert (genie.permissions_object_type, dash.permissions_object_type) == ("genie", "dashboards")
    assert (genie.tag_entity_type, dash.tag_entity_type) == ("geniespaces", "dashboards")
    assert (genie.audience_level, dash.audience_level) == ("CAN_RUN", "CAN_READ")
    assert (genie.has_benchmarks, dash.has_benchmarks) == (True, False)


def test_unknown_kind_is_refused_not_defaulted():
    """A typo'd kind must fail loudly, never quietly promote a dashboard down the Genie path."""
    try:
        rk.get("lakeview_dashboard")
    except ValueError as e:
        assert "unknown resource kind" in str(e)
    else:
        raise AssertionError("an unknown kind must raise")
    # But an absent kind is the documented Genie default (back-compat for pre-seam callers).
    assert rk.get(None).kind == "genie_space"


def test_slug_namespaces_are_disjoint_between_kinds():
    """A dashboard slug must never be mistakable for a Space slug — that disjointness is what lets
    the CI diff and `deployment_attempts.target_ids` hold both kinds without collision."""
    same_id = "01f18061774a1b90bc424bd3c1078591"
    assert app_logic.resource_slug(same_id, "genie_space") == f"s_{same_id}"
    assert app_logic.resource_slug(same_id, "dashboard") == f"d_{same_id}"
    # Genie's pre-seam behaviour is preserved exactly, including the bare-alpha case.
    assert app_logic.space_slug(same_id) == app_logic.resource_slug(same_id, "genie_space")


def test_sidecar_paths_are_per_kind():
    assert app_logic.src_path_for("d_x", "dashboard") == "src/dashboards/d_x.lvdash.json"
    assert app_logic.title_path_for("d_x", "dashboard") == "src/dashboards/d_x.title"
    assert app_logic.audience_path_for("d_x", "dashboard") == "src/dashboards/d_x.audience.json"
    # Genie paths unchanged.
    assert app_logic.src_path_for("s_x") == "src/genie/s_x.serialized_space.json"
    assert app_logic.title_path_for("s_x") == "src/genie/s_x.title"


# --- THE regression this whole slice exists to prevent --------------------------------------------


def test_markdown_url_is_not_reported_as_a_foreign_catalog():
    """A markdown link makes the 3-part-ref grammar match a hostname (`en.wikipedia.org` -> catalog
    `en`). Scanned whole-document, that is a FALSE ENV-01 BLOCKER on a perfectly good dashboard.
    Probed live on a real dev dashboard — this is the trap the structural scan closes."""
    raw = json.dumps(DASHBOARD, ensure_ascii=False)

    # The OLD whole-document behaviour would have flagged the hostname.
    assert "en" in pre_render.find_violations(raw, "prod", "recebiveis")

    # The dashboard scan looks only at dataset SQL: the hostname is gone...
    sql_only = pre_render.scan_text(raw, sql_only=True)
    violations = pre_render.find_violations(sql_only, "prod", "recebiveis")
    assert "en" not in violations
    # ...while a REAL dev-catalog leak inside a dataset query is still caught.
    assert "dev_recebiveis" in violations


def test_dev_catalog_in_dataset_sql_still_blocks_after_rebind_is_skipped():
    """The narrowed scan must not become a way to smuggle a foreign catalog into prod: a `sbx_`
    catalog in a dataset query is still a violation."""
    doc = json.loads(json.dumps(DASHBOARD))
    doc["datasets"][0]["queryLines"] = ["SELECT 1 FROM sbx_recebiveis.diamond.f"]
    scanned = pre_render.scan_text(json.dumps(doc), sql_only=True)
    assert "sbx_recebiveis" in pre_render.find_violations(scanned, "prod", "recebiveis")


def test_prose_catalog_is_rebound_and_reported_as_advisory_never_blocking():
    """A dev catalog written in PROSE is a documentation defect, not a data leak — no query runs from
    a text widget. It must be rebound by the whole-document rebind and surface as advisory DASH-04."""
    rebound = json.loads(pre_render.rebind(json.dumps(DASHBOARD), "dev", "prod", "recebiveis"))

    # The prose was actually rewritten, so the promoted panel reads correctly in prod.
    prose = pre_render.dashboard_prose_text(rebound)
    assert "prod_recebiveis.diamond.fato_recebiveis" in prose
    assert "dev_recebiveis" not in prose

    # The remaining URL surfaces only as an advisory DASH-04 — never a BLOCKER.
    findings = dashboard_check.check_dashboard(rebound, to_env="prod", domain="recebiveis")
    dash04 = [f for f in findings if f["rule_id"] == "DASH-04"]
    assert len(dash04) == 1 and dash04[0]["severity"] == "SUGGESTION"
    assert not [f for f in findings if f["severity"] == "BLOCKER"]


# --- the structural gate (DASH-01..03), the eval-run replacement ----------------------------------


def test_widget_pointing_at_a_missing_dataset_blocks():
    doc = json.loads(json.dumps(DASHBOARD))
    doc["pages"][0]["layout"][0]["widget"]["queries"][0]["query"]["datasetName"] = "ds_gone"
    findings = dashboard_check.check_dashboard(doc)
    blockers = [f for f in findings if f["severity"] == "BLOCKER"]
    assert [f["rule_id"] for f in blockers] == ["DASH-01"]
    assert "ds_gone" in blockers[0]["message"]


def test_dashboard_with_no_widgets_blocks():
    findings = dashboard_check.check_dashboard({"datasets": [], "pages": [{"layout": []}]})
    assert [f["rule_id"] for f in findings if f["severity"] == "BLOCKER"] == ["DASH-03"]


def test_unused_dataset_is_advisory_not_blocking():
    doc = json.loads(json.dumps(DASHBOARD))
    doc["datasets"].append({"name": "ds_dead", "queryLines": ["SELECT 1"]})
    findings = dashboard_check.check_dashboard(doc, to_env="dev", domain="recebiveis")
    dash02 = [f for f in findings if f["rule_id"] == "DASH-02"]
    assert len(dash02) == 1 and dash02[0]["severity"] == "SUGGESTION"
    assert "ds_dead" in dash02[0]["message"]


def test_a_well_formed_dashboard_has_no_findings():
    """The clean case must be genuinely clean — a gate that always fires is a gate nobody trusts."""
    doc = json.loads(pre_render.rebind(json.dumps({
        **DASHBOARD, "pages": [{**DASHBOARD["pages"][0],
                                "layout": [DASHBOARD["pages"][0]["layout"][0]]}],
    }), "dev", "prod", "recebiveis"))
    assert dashboard_check.check_dashboard(doc, to_env="prod", domain="recebiveis") == []


# --- the workspace adapter -------------------------------------------------------------------------


def test_adapter_lists_and_exports_dashboards():
    client = _dashboard_client(dashboards=(("d1", "Painel A"), ("d2", "Painel B")))
    assert wr.list_resources(client, rk.DASHBOARD_KIND) == [
        {"resource_id": "d1", "title": "Painel A"},
        {"resource_id": "d2", "title": "Painel B"},
    ]
    doc = wr.get_serialized(client, rk.DASHBOARD_KIND, "d1")
    assert [d["name"] for d in doc["datasets"]] == ["ds_volume"]


def test_adapter_excludes_trashed_dashboards():
    """A trashed dashboard is not promotable and must not appear in a picker."""
    client = NS(lakeview=NS(list=lambda: [
        NS(dashboard_id="live", display_name="Live", lifecycle_state="ACTIVE"),
        NS(dashboard_id="gone", display_name="Trashed", lifecycle_state="TRASHED"),
    ]))
    assert [r["resource_id"] for r in wr.list_resources(client, rk.DASHBOARD_KIND)] == ["live"]


def test_adapter_refuses_to_guess_between_duplicate_titles():
    """Title is the deploy's only id-resolution key, so an ambiguous title must fail rather than
    reconcile ACLs onto the wrong object. Retrying would not disambiguate, so it raises at once."""
    client = _dashboard_client(dashboards=(("d1", "Mesmo"), ("d2", "Mesmo")))
    try:
        wr.resolve_by_title(client, rk.DASHBOARD_KIND, "Mesmo", max_attempts=1)
    except ValueError as e:
        assert "refusing to guess" in str(e)
    else:
        raise AssertionError("a duplicate title must raise")


def test_adapter_resolves_a_unique_title():
    client = _dashboard_client(dashboards=(("d1", "Único"),))
    assert wr.resolve_by_title(client, rk.DASHBOARD_KIND, "Único", max_attempts=1) == "d1"


# --- authorization: the dashboard guard uses the dashboard ACL, and still fails closed ------------


def test_dashboard_listing_filters_to_what_the_caller_may_access(monkeypatch):
    """The standing dev SP can see EVERY dev dashboard; the app must return only what the VERIFIED
    caller may use (the confused-deputy control A2 exists for)."""
    monkeypatch.setattr(app_logic.authz, "verify_identity",
                        lambda token, host=None: authz.VerifiedIdentity("ana@x.com", frozenset()))
    monkeypatch.setattr(app_logic, "Config", lambda: NS(host="https://prod.example"))
    allowed = _dashboard_client(dashboards=(("d1", "Painel A"),), acl_levels=("CAN_READ",))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: allowed)

    out = app_logic.list_dev_resources("dashboard", user_token="tok")
    assert out == [{"resource_id": "d1", "title": "Painel A"}]


def test_dashboard_listing_drops_a_resource_the_caller_cannot_access(monkeypatch):
    monkeypatch.setattr(app_logic.authz, "verify_identity",
                        lambda token, host=None: authz.VerifiedIdentity("mallory@x.com", frozenset()))
    monkeypatch.setattr(app_logic, "Config", lambda: NS(host="https://prod.example"))
    # The ACL names ana@x.com only, so mallory is denied and the dashboard is dropped.
    denied = _dashboard_client(dashboards=(("d1", "Painel A"),), acl_levels=("CAN_MANAGE",))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: denied)

    assert app_logic.list_dev_resources("dashboard", user_token="tok") == []


def test_dashboard_export_denies_before_using_the_service_principal_reach(monkeypatch):
    """The guard must run BEFORE the export — denying first, never after reading the resource."""
    monkeypatch.setattr(app_logic.authz, "verify_identity",
                        lambda token, host=None: authz.VerifiedIdentity("mallory@x.com", frozenset()))
    monkeypatch.setattr(app_logic, "Config", lambda: NS(host="https://prod.example"))
    exported = []

    def _get(dashboard_id):
        exported.append(dashboard_id)
        return NS(serialized_dashboard="{}")

    client = NS(lakeview=NS(get=_get, list=lambda: []),
                permissions=NS(get=lambda **kw: NS(access_control_list=[])))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: client)

    try:
        app_logic.export_serialized("d1", user_token="tok", kind="dashboard")
    except authz.AccessDenied:
        pass
    else:
        raise AssertionError("a denied caller must not get an export")
    assert exported == [], "the export ran despite the guard denying"


def test_access_check_failure_denies_rather_than_allowing(monkeypatch):
    """Fail closed: an ACL read error is a denial, never an allow."""
    from databricks.sdk.errors import DatabricksError

    def _boom(**kw):
        raise DatabricksError("dev unreachable")

    identity = authz.VerifiedIdentity("ana@x.com", frozenset())
    try:
        authz.assert_can_access(identity, "d1", transport=NS(permissions=NS(get=_boom)),
                                object_type="dashboards")
    except authz.AccessDenied:
        pass
    else:
        raise AssertionError("an ACL read failure must deny")


# --- audience checking over a dashboard's SQL-derived tables --------------------------------------


def test_audience_check_reads_dashboard_tables_from_dataset_sql():
    """A dashboard declares no tables structurally — they exist only inside dataset SQL."""
    rebound = json.loads(pre_render.rebind(json.dumps(DASHBOARD), "dev", "prod", "recebiveis"))
    tables = audience_check.dashboard_tables(rebound)
    assert set(tables) == {
        "prod_recebiveis.diamond.fato_recebiveis",
        "prod_recebiveis.diamond.dim_arranjo",
    }
    # Crucially, the markdown hostname is NOT offered up as a table to validate.
    assert not any("wikipedia" in t for t in tables)


def test_audience_check_blocks_a_missing_principal_with_the_dashboard_level():
    findings = audience_check.check_audience(
        {"datasets": [], "pages": []}, _audience(), lambda fq: [],
        principal_exists=lambda name, is_group: False,
        tables_of=audience_check.dashboard_tables,
        audience_level="CAN_READ",
    )
    blockers = [f for f in findings if f["severity"] == "BLOCKER"]
    assert len(blockers) == 1 and "CAN_READ" in blockers[0]["message"]


# --- review + promote: the whole flow for a dashboard ---------------------------------------------


class _FakeGitHubApp:
    def __init__(self):
        self.promo = None
        self.comment = None

    def open_or_update_promotion(self, **kw):
        self.promo = kw
        return {"number": 42, "html_url": "https://github.com/o/r/pull/42"}

    def get_file_content(self, path, **_kwargs):
        return "a" * 40 if path == "engine.lock" else None

    def post_review_comment(self, number, marker, body):
        self.comment = {"number": number, "body": body}
        return {"id": 1}

    def apply_blocker_label(self, number):
        pass

    def clear_blocker_label(self, number):
        pass


_DASH_REVIEW = {
    "findings": [],
    "gate": {"conclusion": "success", "blocker_count": 0, "summary": "🟢 pronta"},
    "eval": {"status": "advisory", "summary": "sem benchmarks"},
    "timeline": [],
    "allowlist_violations": [],
    "audience_spec": _audience().to_dict(),
    "prod_serialized": {},
    "dev_serialized": DASHBOARD,
}


def test_dashboard_promotion_commits_the_lvdash_sidecar_set(monkeypatch):
    """The committed file set IS the content contract the CI render and deploy read."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_DASH_REVIEW))
    gh = _FakeGitHubApp()
    out = app_logic.request_promotion(
        "01f1806177", user_token="tok", requester_email="ana@x.com",
        resource_title="Painel de Recebíveis", audience_spec_=_audience(), github=gh,
        kind="dashboard")

    assert out["pr"] == {"number": 42, "url": "https://github.com/o/r/pull/42"}
    slug = "d_01f1806177"
    assert gh.promo["branch"] == f"promote/{slug}"
    assert gh.promo["path"] == f"src/dashboards/{slug}.lvdash.json"
    committed = set(gh.promo["extra_files"]) | {gh.promo["path"]}
    assert committed == {
        f"src/dashboards/{slug}.lvdash.json",
        f"src/dashboards/{slug}.title",
        f"src/dashboards/{slug}.audience.json",
        f"src/dashboards/{slug}.revision.json",
    }
    # The title sidecar carries the DECLARED prod name — it is the deploy's id-resolution key.
    assert gh.promo["extra_files"][f"src/dashboards/{slug}.title"] == "Painel de Recebíveis\n"
    # The artifact committed is the DEV-shaped export (CI rebinds it), byte-for-byte what was reviewed.
    assert json.loads(gh.promo["path"] and gh.promo["content"]) == DASHBOARD
    # The PR is worded for a dashboard so the reviewer knows what they are merging.
    assert "Painel AI/BI" in gh.promo["title"]


def test_dashboard_promotion_is_a_draft_and_never_marked_ready(monkeypatch):
    """The bot may open or re-draft, never mark ready — that is the human's act of promoting."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_DASH_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("d1", user_token="tok", audience_spec_=_audience(), github=gh,
                                kind="dashboard")
    assert not hasattr(gh, "marked_ready")
    assert "rascunho" in gh.promo["body"].lower()


def test_stale_mapping_sidecar_is_removed_when_no_mapping_is_declared(monkeypatch):
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_DASH_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("d1", user_token="tok", audience_spec_=_audience(), github=gh,
                                kind="dashboard")
    assert gh.promo["remove_files"] == ["src/dashboards/d_d1.mapping.json"]


def test_dashboard_review_runs_structural_checks_and_no_eval_run(monkeypatch):
    """The dashboard quality story: DASH-* run, and the eval-run is reported not-applicable rather
    than attempted (a dashboard has no benchmarks to run)."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: DASHBOARD)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary":"ok","findings":[]}')

    def _must_not_run(*a, **k):
        raise AssertionError("an eval-run must never be attempted for a dashboard")

    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest", _must_not_run)

    out = app_logic.review_space("d1", profile="p", audience_spec_=_audience(), kind="dashboard")

    assert out["eval"]["status"] == "advisory"
    assert "benchmark" in out["eval"]["summary"].lower()
    # No EVAL-01 backstop was synthesized for a kind that has no benchmarks.
    assert not [f for f in out["findings"] if f["rule_id"] == "EVAL-01"]
    # The quality step in the timeline names the dashboard checks, not an eval-run.
    keys = [s["key"] for s in out["timeline"]]
    assert "structure" in keys and "eval" not in keys


def test_dashboard_review_blocks_on_a_structural_defect(monkeypatch):
    broken = json.loads(json.dumps(DASHBOARD))
    broken["pages"][0]["layout"][0]["widget"]["queries"][0]["query"]["datasetName"] = "ds_gone"
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: broken)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary":"ok","findings":[]}')

    out = app_logic.review_space("d1", profile="p", audience_spec_=_audience(), kind="dashboard")

    assert out["gate"]["conclusion"] == "failure"
    assert "DASH-01" in {f["rule_id"] for f in out["findings"]}
    assert [s for s in out["timeline"] if s["key"] == "structure"][0]["status"] == "fail"


def test_genie_review_is_unchanged_by_the_kind_seam(monkeypatch):
    """The acceptance bar for the whole slice: a Genie review still gets its benchmark backstop and
    still attempts its eval-run."""
    space = {"data_sources": {"tables": []}, "instructions": {}, "benchmarks": {"questions": []}}
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: space)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary":"ok","findings":[]}')
    attempted = []
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: attempted.append(1) or {"status": "advisory", "summary": "x"})

    out = app_logic.review_space("s1", profile="p", audience_spec_=_audience())

    assert attempted == [1], "the Genie eval-run must still be attempted"
    assert "EVAL-01" in {f["rule_id"] for f in out["findings"]}
    keys = [s["key"] for s in out["timeline"]]
    assert "eval" in keys and "structure" not in keys


def test_promote_preview_offers_only_real_tables_for_a_dashboard(monkeypatch):
    """The de-para must not offer the caller a markdown hostname as a table to remap."""
    client = _dashboard_client()
    out = app_logic.preview_promotion("d1", user_token="tok", dev_client=client, kind="dashboard")
    sources = {t["source"] for t in out["tables"]}
    assert sources == {"dev_recebiveis.diamond.fato_recebiveis",
                       "dev_recebiveis.diamond.dim_arranjo"}
    assert out["tables"][0]["default_target"].startswith("prod_recebiveis.")
    assert out["title"] == "Painel Recebíveis"


def test_all_dev_resources_merges_kinds_into_one_discriminated_list(monkeypatch):
    """"Meus espaços" is ONE list: the DTO carries the kind so the UI needs no per-kind fetch."""
    monkeypatch.setattr(app_logic, "list_dev_resources", lambda kind=None, profile=None, **k: {
        "genie_space": [{"resource_id": "s1", "title": "Espaço"}],
        "dashboard": [{"resource_id": "d1", "title": "Painel"}],
    }[kind])

    out = app_logic.list_all_dev_resources()
    assert out == [
        {"id": "s1", "title": "Espaço", "kind": "genie_space", "env": "dev"},
        {"id": "d1", "title": "Painel", "kind": "dashboard", "env": "dev"},
    ]


def test_one_kind_failing_does_not_blank_the_whole_resource_list(monkeypatch):
    """A workspace where one kind's API errors must still show the other kind — availability, not a
    security decision (per-resource access is enforced independently and fails closed)."""
    def _list(kind=None, profile=None, **k):
        if kind == "dashboard":
            raise RuntimeError("lakeview unavailable")
        return [{"resource_id": "s1", "title": "Espaço"}]

    monkeypatch.setattr(app_logic, "list_dev_resources", _list)
    assert [r["kind"] for r in app_logic.list_all_dev_resources()] == ["genie_space"]

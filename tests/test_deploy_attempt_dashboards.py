"""The ADR-0007 deploy stages over BOTH resource kinds.

The stage NAMES are a persisted contract (`deployment_attempts.completed_stages`, rendered in the app),
so adding dashboards must not add stages — each stage instead iterates every kind's artifacts. These
tests pin exactly that, plus the two facts that are easy to get wrong and impossible to notice until
production: the permissions object type and the tag entity type.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

from databricks.sdk.errors import NotFound
from databricks.sdk.service.tags import TagAssignment

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))

import deploy_attempt  # noqa: E402
import resource_kind as rk  # noqa: E402

AUDIENCE = {"principals": [{"principal": "users", "is_group": True}]}
DASHBOARD = {
    "datasets": [{"name": "ds", "queryLines": ["SELECT 1 FROM prod_recebiveis.diamond.f"]}],
    "pages": [{"name": "p", "layout": [{"widget": {"name": "w", "queries": [
        {"query": {"datasetName": "ds"}}]}}]}],
}
SPACE = {"data_sources": {"tables": []}}


# The dashboard slug is `<area>/<name>` (the NESTED layout); a Genie slug stays flat.
DASH_SLUG = "risco/volume_por_bandeira"


def _build(tmp_path: Path, *, spaces=(("receivables", "Recebíveis"),),
           dashboards=((DASH_SLUG, "Painel de Recebíveis"),)) -> Path:
    """A rendered build/ tree containing artifacts of both kinds, each in ITS OWN layout.

    Genie is FLAT (`build/genie/<slug>.serialized_space.json` + `<slug>.`-prefixed sidecars); a
    dashboard is NESTED (`build/dashboards/<area>/<name>/dashboard.lvdash.json` + fixed sidecar
    names). Building both shapes here is what makes the per-kind discovery genuinely exercised.
    """
    genie_base = tmp_path / "build" / "genie"
    genie_base.mkdir(parents=True, exist_ok=True)
    for slug, title in spaces:
        (genie_base / f"{slug}.serialized_space.json").write_text(json.dumps(SPACE), encoding="utf-8")
        (genie_base / f"{slug}.title").write_text(title + "\n", encoding="utf-8")
        (genie_base / f"{slug}.audience.json").write_text(json.dumps(AUDIENCE), encoding="utf-8")

    dash_base = tmp_path / "build" / "dashboards"
    dash_base.mkdir(parents=True, exist_ok=True)
    for slug, title in dashboards:
        resource_dir = dash_base / slug
        resource_dir.mkdir(parents=True, exist_ok=True)
        (resource_dir / "dashboard.lvdash.json").write_text(json.dumps(DASHBOARD), encoding="utf-8")
        (resource_dir / "title").write_text(title + "\n", encoding="utf-8")
        (resource_dir / "audience.json").write_text(json.dumps(AUDIENCE), encoding="utf-8")
    return tmp_path


class _Permissions:
    """Records every permissions call with its object type — the fact most worth pinning.

    The ACL it reports back is per OBJECT TYPE, mirroring reality: a Genie space's audience sits at
    CAN_RUN, a dashboard's at CAN_READ. A single fixed level would make one kind's readback fail for a
    reason that has nothing to do with the code under test.
    """

    def __init__(self, level_by_object=None):
        self.updates = []
        self.sets = []
        self.gets = []
        self.level_by_object = level_by_object or {"genie": "CAN_RUN", "dashboards": "CAN_READ"}

    def _acl(self, object_type):
        return NS(access_control_list=[
            NS(user_name=None, group_name="users", service_principal_name=None,
               all_permissions=[NS(permission_level=self.level_by_object[object_type],
                                   inherited=False)]),
            NS(user_name=None, group_name=None, service_principal_name="app-sp",
               all_permissions=[NS(permission_level="CAN_MANAGE", inherited=False)]),
        ])

    def get(self, request_object_type, request_object_id):
        self.gets.append((request_object_type, request_object_id))
        return self._acl(request_object_type)

    def update(self, request_object_type, request_object_id, access_control_list):
        self.updates.append((request_object_type, request_object_id))

    def set(self, request_object_type, request_object_id, access_control_list):
        self.sets.append((request_object_type, request_object_id,
                          [getattr(a.permission_level, "value", a.permission_level)
                           for a in access_control_list]))
        return self._acl(request_object_type)


class _Tags:
    def __init__(self):
        self.store = {}
        self.calls = []

    def get_tag_assignment(self, entity_type, entity_id, tag_key):
        self.calls.append(("get", entity_type, entity_id))
        key = (entity_type, entity_id, tag_key)
        if key not in self.store:
            raise NotFound("no tag")
        return TagAssignment(entity_type=entity_type, entity_id=entity_id, tag_key=tag_key,
                             tag_value=self.store[key])

    def create_tag_assignment(self, assignment):
        self.calls.append(("create", assignment.entity_type, assignment.entity_id))
        self.store[(assignment.entity_type, assignment.entity_id,
                    assignment.tag_key)] = assignment.tag_value

    def update_tag_assignment(self, entity_type, entity_id, tag_key, assignment, update_mask):
        self.calls.append(("update", entity_type, entity_id))
        self.store[(entity_type, entity_id, tag_key)] = assignment.tag_value


def _operations(tmp_path: Path, *, permissions=None, tags=None, allow_destructive=False,
                allow_empty_content=False):
    permissions = permissions or _Permissions()
    tags = tags or _Tags()
    client = NS(
        current_user=NS(me=lambda: NS(id="ci-sp")),
        apps=NS(get=lambda _n: NS(service_principal_client_id="app-sp")),
        permissions=permissions,
        workspace_entity_tag_assignments=tags,
        # `get_space`/`get` are needed because resolution PROBES a unique match before accepting it:
        # after a recreate the listing can serve a deleted id, and handing that tombstone to the ACL
        # and tag stages is what broke deploy run 30573544213.
        genie=NS(list_spaces=lambda: NS(spaces=[NS(space_id="space-1", title="Recebíveis")]),
                 get_space=lambda sid, **k: NS(space_id=sid, title="Recebíveis")),
        lakeview=NS(list=lambda: [NS(dashboard_id="dash-1", display_name="Painel de Recebíveis",
                                     lifecycle_state="ACTIVE")],
                    get=lambda did: NS(dashboard_id=did, display_name="Painel de Recebíveis")),
    )
    ops = deploy_attempt.ProductionOperations(
        tmp_path, "wh-1", client=client, certification_readback_retry_seconds=0,
        certification_readback_sleep=lambda _s: None, allow_destructive=allow_destructive,
        allow_empty_content=allow_empty_content)
    return ops, permissions, tags


# --- artifact discovery ---------------------------------------------------------------------------


def test_artifacts_are_discovered_for_both_kinds(tmp_path):
    ops, _p, _t = _operations(_build(tmp_path))
    found = {(kind.kind, slug) for kind, slug, _r, _ti, _a in ops._all_artifacts()}
    assert found == {("genie_space", "receivables"), ("dashboard", DASH_SLUG)}


def test_a_dashboard_only_promotion_is_valid(tmp_path):
    """A promotion containing no Genie Space at all must deploy — previously this raised."""
    ops, _p, _t = _operations(_build(tmp_path, spaces=()))
    kinds = {kind.kind for kind, *_rest in ops._all_artifacts()}
    assert kinds == {"dashboard"}


def test_an_entirely_empty_content_tree_still_fails_closed(tmp_path):
    """An empty desired state must never read as "deploy nothing" — for a bundle it reads as
    "delete the managed content"."""
    (tmp_path / "build" / "genie").mkdir(parents=True)
    (tmp_path / "build" / "dashboards").mkdir(parents=True)
    ops, _p, _t = _operations(tmp_path)
    try:
        ops._all_artifacts()
    except ValueError as e:
        assert "no rendered promotable artifacts" in str(e)
    else:
        raise AssertionError("an empty content tree must fail closed")


def _empty_tree(tmp_path: Path) -> Path:
    (tmp_path / "build" / "genie").mkdir(parents=True)
    (tmp_path / "build" / "dashboards").mkdir(parents=True)
    return tmp_path


def test_an_empty_tree_is_refused_when_only_emptiness_is_authorized(tmp_path):
    """`allow_empty_content` ALONE must not open the decommission path.

    Emptying the content tree IS "delete the managed content", so it is only honoured together with
    the destructive flag — the two-key rule. One key is still a refusal.
    """
    ops, _p, _t = _operations(_empty_tree(tmp_path), allow_empty_content=True)
    try:
        ops._all_artifacts()
    except ValueError as e:
        assert "no rendered promotable artifacts" in str(e)
    else:
        raise AssertionError("emptiness alone must not authorize a decommission")


def test_an_empty_tree_is_refused_when_only_destruction_is_authorized(tmp_path):
    """The other half of the two-key rule: `allow_destructive` is set for ordinary key renames, and
    must NOT by itself turn an accidentally-emptied content tree into a full decommission."""
    ops, _p, _t = _operations(_empty_tree(tmp_path), allow_destructive=True)
    try:
        ops._all_artifacts()
    except ValueError as e:
        assert "no rendered promotable artifacts" in str(e)
    else:
        raise AssertionError("a destructive run must not silently accept an empty tree")


def test_an_empty_tree_is_allowed_when_both_keys_are_turned(tmp_path, capsys):
    """The deliberate decommission: both flags set yields an empty desired set, loudly warned."""
    ops, _p, _t = _operations(_empty_tree(tmp_path), allow_destructive=True,
                              allow_empty_content=True)
    assert ops._all_artifacts() == []
    assert "deploy-empty-content" in capsys.readouterr().out


def test_a_slug_shared_across_kinds_is_refused(tmp_path):
    """REGRESSION: a slug must identify exactly one resource across ALL kinds.

    Prefixes (`s_*`/`d_*`) only make GENERATED slugs disjoint — a PINNED friendly slug carries none, so
    a collision is reachable by configuration. It would be silently harmful: `resolve_space` keys one
    dict by slug (the later kind wins the id) while `_kind_of` returns the FIRST match, so a stage
    would apply Genie's permissions object type / tag entity type to a dashboard's id.
    """
    # A PINNED friendly slug carries no `<area>/<name>` shape, so a Genie slug and a dashboard slug
    # can collide by configuration — that is the case this must refuse.
    repo = _build(tmp_path, spaces=(("recebiveis", "Espaço"),),
                  dashboards=(("recebiveis", "Painel"),))
    ops, _p, _t = _operations(repo)
    try:
        ops._all_artifacts()
    except ValueError as e:
        assert "unique across resource kinds" in str(e)
    else:
        raise AssertionError("a slug shared by two kinds must fail loud")


def test_a_dashboard_without_a_title_sidecar_fails(tmp_path):
    repo = _build(tmp_path)
    (repo / "build" / "dashboards" / DASH_SLUG / "title").unlink()
    ops, _p, _t = _operations(repo)
    try:
        ops._all_artifacts()
    except ValueError as e:
        assert "title" in str(e)
    else:
        raise AssertionError("a missing title sidecar must fail")


# --- the stages -----------------------------------------------------------------------------------


def test_resolve_space_resolves_both_kinds_by_title(tmp_path):
    ops, _p, _t = _operations(_build(tmp_path))
    assert ops.resolve_space() == {"receivables": "space-1", DASH_SLUG: "dash-1"}


def test_app_manage_uses_each_kind_permissions_object_type(tmp_path):
    """`genie` vs `dashboards` — the plural is required for dashboards; the singular is rejected by
    the API, so a regression here is invisible offline and fatal in production."""
    ops, permissions, _t = _operations(_build(tmp_path))
    ops.assert_app_manage({"receivables": "space-1", DASH_SLUG: "dash-1"})
    assert set(permissions.updates) == {("genie", "space-1"), ("dashboards", "dash-1")}


def test_reconcile_audience_derives_the_level_of_each_kind(tmp_path):
    """A Space audience derives CAN_RUN; a dashboard audience derives CAN_READ — the least level that
    lets a business user open a published dashboard."""
    ops, permissions, _t = _operations(_build(tmp_path))
    ops.reconcile_audience({"receivables": "space-1", DASH_SLUG: "dash-1"})
    by_object = {(obj, rid): levels for obj, rid, levels in permissions.sets}
    assert "CAN_RUN" in by_object[("genie", "space-1")]
    assert "CAN_READ" in by_object[("dashboards", "dash-1")]


def test_verify_live_state_reads_back_through_each_kind_object_type(tmp_path):
    ops, permissions, _t = _operations(_build(tmp_path))
    ops.verify_live_state({"receivables": "space-1", DASH_SLUG: "dash-1"})
    assert ("dashboards", "dash-1") in permissions.gets
    assert ("genie", "space-1") in permissions.gets


def test_a_genie_readback_of_can_read_still_fails(tmp_path):
    """REGRESSION: the audience readback's sufficient set must be PER KIND.

    `CAN_READ` IS assignable on object type `genie` (verified live), so a shared set would make a Genie
    readback ACCEPT a principal holding only CAN_READ — weaker than before dashboards existed. The
    dashboard readback must accept it; the Genie one must not.
    """
    both_can_read = _Permissions(level_by_object={"genie": "CAN_READ", "dashboards": "CAN_READ"})
    ops, _p, _t = _operations(_build(tmp_path), permissions=both_can_read)
    try:
        ops.verify_live_state({"receivables": "space-1", DASH_SLUG: "dash-1"})
    except RuntimeError as e:
        assert "audience readback failed" in str(e)
        assert "receivables" in str(e), "the GENIE slug must be the one that fails"
    else:
        raise AssertionError("a Genie principal holding only CAN_READ must fail the readback")


def test_a_dashboard_readback_of_can_read_passes(tmp_path):
    """The other half: CAN_READ is exactly what a dashboard audience derives, so it must satisfy."""
    ops, _p, _t = _operations(
        _build(tmp_path, spaces=()),
        permissions=_Permissions(level_by_object={"genie": "CAN_RUN", "dashboards": "CAN_READ"}))
    ops.verify_live_state({DASH_SLUG: "dash-1"})  # must not raise


def test_verify_live_state_fails_when_the_audience_is_absent(tmp_path):
    """Readback — not the write's return value — is the evidence."""
    permissions = _Permissions()
    permissions._acl = lambda _object_type: NS(access_control_list=[
        NS(user_name=None, group_name=None, service_principal_name="app-sp",
           all_permissions=[NS(permission_level="CAN_MANAGE", inherited=False)])])
    ops, _p, _t = _operations(_build(tmp_path), permissions=permissions)
    try:
        ops.verify_live_state({"receivables": "space-1", DASH_SLUG: "dash-1"})
    except RuntimeError as e:
        assert "audience readback failed" in str(e)
    else:
        raise AssertionError("a missing audience must fail the deploy")


def test_certification_uses_each_kind_tag_entity_type(tmp_path):
    """`geniespaces` vs `dashboards` — the other spellings are rejected outright by the tag API."""
    ops, _p, tags = _operations(_build(tmp_path))
    ops.certify_space({"receivables": "space-1", DASH_SLUG: "dash-1"})

    assert tags.store[("geniespaces", "space-1", "system.certification_status")] == "certified"
    assert tags.store[("dashboards", "dash-1", "system.certification_status")] == "certified"


def test_certification_readback_tolerates_eventual_consistency(tmp_path):
    """Tag propagation is eventually consistent: the first read after a write can legitimately miss.
    That is not a permission problem, so it must be retried rather than failing the deploy."""
    class _FlakyTags(_Tags):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def get_tag_assignment(self, entity_type, entity_id, tag_key):
            self.reads += 1
            if self.reads == 2:  # the first post-create readback
                raise NotFound("not yet visible")
            return super().get_tag_assignment(entity_type, entity_id, tag_key)

    tags = _FlakyTags()
    ops, _p, _t = _operations(_build(tmp_path, spaces=()), tags=tags)
    ops.certify_space({DASH_SLUG: "dash-1"})
    assert tags.store[("dashboards", "dash-1", "system.certification_status")] == "certified"


def test_previous_audience_is_read_from_the_right_source_dir(tmp_path):
    """Reconciliation needs the PREVIOUS spec to know what it may remove; a dashboard's lives under
    src/dashboards, not src/genie."""
    repo = _build(tmp_path)
    previous = tmp_path / "previous"
    (previous / "src" / "dashboards" / DASH_SLUG).mkdir(parents=True)
    (previous / "src" / "dashboards" / DASH_SLUG / "audience.json").write_text(
        json.dumps({"principals": [{"principal": "antigo", "is_group": False}]}), encoding="utf-8")
    ops, _p, _t = _operations(repo)
    ops.previous_content_root = previous

    spec = ops._previous(DASH_SLUG, rk.DASHBOARD_KIND)
    assert spec is not None and spec.names() == ("antigo",)
    # And a Genie slug still reads from src/genie (absent here -> None, not a crash).
    assert ops._previous("receivables", rk.GENIE_SPACE_KIND) is None


def test_stage_names_are_unchanged_by_adding_a_resource_kind():
    """They are persisted in `deployment_attempts.completed_stages` and rendered in the app, so they
    are a contract: a second kind iterates within the stages, it does not add any."""
    assert deploy_attempt.MUTATION_STAGES == (
        "bundle_deploy", "resolve_space", "assert_app_manage", "reconcile_audience",
        "verify_live_state", "certify_space", "complete",
    )


# --- destructive deploys are opt-in, never a standing default --------------------------------------


def _captured_deploy(tmp_path: Path, **kwargs):
    ops = deploy_attempt.ProductionOperations(tmp_path, "wh-1", client=NS(), **kwargs)
    calls = []
    ops._run = lambda *a: calls.append(a)
    ops.bundle_deploy()
    return calls[0]


def test_bundle_deploy_never_auto_approves_by_default(tmp_path):
    """A deploy that would DELETE or RECREATE a managed resource must fail closed with the CLI's plan
    in the log. Recreating a dashboard changes its id and permanent URL, so the refusal is the desired
    behaviour — observed live in run 30572177435, where a resource-key rename read as delete+create."""
    argv = _captured_deploy(tmp_path)
    assert "--auto-approve" not in argv
    assert argv[:4] == ("databricks", "bundle", "deploy", "-t")


def test_bundle_deploy_auto_approves_only_when_explicitly_allowed(tmp_path):
    """A legitimate destructive change (renaming a resource key) needs an escape hatch — but it must be
    an explicit, per-run decision by whoever authorized it."""
    argv = _captured_deploy(tmp_path, allow_destructive=True)
    assert "--auto-approve" in argv

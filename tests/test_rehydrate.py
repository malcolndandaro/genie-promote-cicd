"""Unit tests for app/rehydrate.py (A3/F1 — prod->dev rehydrate).

No live workspace: prod/dev transports are fakes shaped like the real SDK objects (mirrors
tests/test_app_logic.py + tests/test_authz.py conventions). `assert_can_access` is REUSED, never
re-implemented — these tests assert both blast sites (source-prod-read, overwrite-target-dev) go
through it, and that the SP2 failure-class disambiguation (re-bootstrap vs. a normal 403) holds.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import app_logic  # noqa: E402
import authz  # noqa: E402
import promotion_store  # noqa: E402
import rehydrate  # noqa: E402
from databricks.sdk.errors import NotFound  # noqa: E402

ANA = authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())
MALLORY = authz.VerifiedIdentity(user_name="mallory@x", group_names=frozenset())

PROD_SERIALIZED = {
    "version": 1,
    "data_sources": {"tables": [{"identifier": "prod_recebiveis.diamond.fato_recebiveis"}]},
    "benchmarks": {"questions": [
        {"id": "11111111-1111-1111-1111-111111111111", "question": ["qtd?"],
         "answer": [{"format": "SQL", "content": ["SELECT * FROM prod_recebiveis.diamond.fato_recebiveis"]}]},
    ]},
}


def _acl_transport(allowed_by_space: dict, *, genie=None):
    """A fake WorkspaceClient-shaped transport: `permissions.get` answers per-space from
    `allowed_by_space` (space_id -> [user_names]); `genie` (if given) is attached as-is."""
    def get_perms(request_object_type, request_object_id):
        allowed = allowed_by_space.get(request_object_id, [])
        return NS(access_control_list=[
            NS(user_name=u, group_name=None, service_principal_name=None,
               all_permissions=[NS(permission_level="CAN_MANAGE")])
            for u in allowed
        ])

    ns = NS(permissions=NS(get=get_perms))
    if genie is not None:
        ns.genie = genie
    return ns


def _prod_transport(space_id, allowed, serialized=PROD_SERIALIZED):
    genie = NS(get_space=lambda sid, include_serialized_space=False:
               NS(serialized_space=json.dumps(serialized, ensure_ascii=False)))
    return _acl_transport({space_id: allowed}, genie=genie)


def setup_function(_fn):
    os.environ.pop("APP_DEV_WAREHOUSE_ID", None)
    rehydrate.APP_DEV_WAREHOUSE_ID = "dev-wh-1"
    rehydrate.APP_DEV_PARENT_PATH = None


# --- create mode -------------------------------------------------------------------------------


def test_create_mode_exports_rebinds_and_creates_in_dev():
    prod = _prod_transport("prod-space", ["ana@x"])
    created = {}

    def create_space(warehouse_id, serialized_space, *, title=None, parent_path=None):
        created.update(warehouse_id=warehouse_id, serialized_space=serialized_space,
                       title=title, parent_path=parent_path)
        return NS(space_id="new-dev-space")

    dev = _acl_transport({}, genie=NS(create_space=create_space))

    result = rehydrate.rehydrate_space(
        source_prod_space_id="prod-space", identity=ANA, mode="create", title="Receivables (dev)",
        prod_client=prod, dev_client=dev)

    assert result.space_id == "new-dev-space"
    assert result.mode == "create"
    assert created["warehouse_id"] == "dev-wh-1"
    assert created["title"] == "Receivables (dev)"
    body = json.loads(created["serialized_space"])
    assert body["data_sources"]["tables"][0]["identifier"] == "dev_recebiveis.diamond.fato_recebiveis"


def test_create_mode_benchmarks_survive_the_rebind():
    prod = _prod_transport("prod-space", ["ana@x"])
    created = {}
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: (
        created.update(serialized_space=ss), NS(space_id="new-id"))[-1]))

    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev)
    body = json.loads(created["serialized_space"])
    qs = body["benchmarks"]["questions"]
    assert len(qs) == 1
    assert qs[0]["id"] == "11111111-1111-1111-1111-111111111111"
    assert "dev_recebiveis" in qs[0]["answer"][0]["content"][0]


# --- overwrite mode ------------------------------------------------------------------------------


def test_overwrite_mode_calls_update_space_with_every_reset_field():
    prod = _prod_transport("prod-space", ["ana@x"])
    updated = {}

    def update_space(space_id, *, serialized_space=None, warehouse_id=None, title=None):
        updated.update(space_id=space_id, serialized_space=serialized_space,
                       warehouse_id=warehouse_id, title=title)

    dev = _acl_transport({"dev-space": ["ana@x"]}, genie=NS(update_space=update_space))

    result = rehydrate.rehydrate_space(
        source_prod_space_id="prod-space", identity=ANA, mode="overwrite",
        dev_space_id="dev-space", title="Receivables (dev)", prod_client=prod, dev_client=dev)

    assert result.space_id == "dev-space"
    assert result.mode == "overwrite"
    # SP1: PATCH semantics -> every field we want reset must be passed explicitly.
    assert updated["space_id"] == "dev-space"
    assert updated["warehouse_id"] == "dev-wh-1"
    assert updated["title"] == "Receivables (dev)"
    assert updated["serialized_space"] is not None


def test_overwrite_denied_when_caller_cannot_access_target_dev_space():
    # THE A3 acceptance criterion: overwrite target resolution goes through assert_can_access.
    # The caller can read the SOURCE prod space but does NOT own the TARGET dev space.
    prod = _prod_transport("prod-space", ["ana@x"])
    called = {"update": False}
    dev = _acl_transport({"dev-space": ["someone-else@x"]},
                         genie=NS(update_space=lambda *a, **k: called.__setitem__("update", True)))

    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="overwrite",
                                  dev_space_id="dev-space", prod_client=prod, dev_client=dev)
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass
    assert called["update"] is False  # denied BEFORE ever touching dev's update_space


def test_overwrite_requires_dev_space_id():
    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="overwrite")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "dev_space_id" in str(e)


# --- source-prod-read guard: denies BEFORE touching dev -------------------------------------------


def test_source_prod_read_denied_before_touching_dev():
    # mallory cannot access the source prod space; dev must never be reached (not even to check the
    # dev SP's reachability) once the export itself is denied.
    prod = _prod_transport("prod-space", ["ana@x"])
    dev_touched = {"value": False}

    class _PoisonedDev:
        def __getattr__(self, name):
            dev_touched["value"] = True
            raise AssertionError(f"dev transport must not be touched: {name}")

    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=MALLORY, mode="create",
                                  prod_client=prod, dev_client=_PoisonedDev())
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass
    assert dev_touched["value"] is False


# --- invalid mode ----------------------------------------------------------------------------


def test_invalid_mode_rejected():
    try:
        rehydrate.rehydrate_space(source_prod_space_id="x", identity=ANA, mode="delete")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "mode" in str(e)


# --- reverse allowlist: a stray prod_/foreign catalog must never reach dev ------------------------


def test_reverse_allowlist_catches_a_stray_prod_ref_after_rebind():
    # Simulate a payload with a foreign/stray catalog reference that survives the prod_->dev_ rebind
    # untouched (e.g. a second customer's prod catalog embedded in example SQL) — find_violations
    # (to_env="dev") must catch it and refuse the rehydrate rather than writing it into dev.
    poisoned = {
        "data_sources": {"tables": [{"identifier": "prod_recebiveis.diamond.fato_recebiveis"}]},
        "instructions": {"example_question_sqls": [
            {"sql": ["SELECT * FROM prod_other_customer.diamond.t"]}
        ]},
    }
    prod = _prod_transport("prod-space", ["ana@x"], serialized=poisoned)
    dev = _acl_transport({}, genie=NS(create_space=lambda *a, **k: NS(space_id="never-created")))

    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                                  prod_client=prod, dev_client=dev)
        assert False, "expected ValueError (reverse allowlist violation)"
    except ValueError as e:
        assert "prod_other_customer" in str(e)


# --- dev warehouse config-driven -------------------------------------------------------------


def test_missing_dev_warehouse_id_raises():
    rehydrate.APP_DEV_WAREHOUSE_ID = None
    prod = _prod_transport("prod-space", ["ana@x"])
    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                                  prod_client=prod, dev_client=NS())
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "APP_DEV_WAREHOUSE_ID" in str(e)


def test_explicit_dev_warehouse_id_overrides_config():
    prod = _prod_transport("prod-space", ["ana@x"])
    created = {}
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: (
        created.update(warehouse_id=wh), NS(space_id="x"))[-1]))
    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, dev_warehouse_id="explicit-wh")
    assert created["warehouse_id"] == "explicit-wh"


# --- SP2 disambiguation: re-bootstrap vs. a normal 403 ----------------------------------------


def test_dev_sp_unreachable_raises_bootstrap_error_not_access_denied(monkeypatch):
    # No dev_client injected -> falls through to app_logic._client(scope="dev-sp"), which raises a
    # plain RuntimeError when APP_DEV_HOST isn't configured. rehydrate must translate that into the
    # DISTINCT DevEnvironmentNotBootstrapped, never authz.AccessDenied (SP2's core disambiguation).
    prod = _prod_transport("prod-space", ["ana@x"])
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", None)

    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                                  prod_client=prod)
        assert False, "expected DevEnvironmentNotBootstrapped"
    except rehydrate.DevEnvironmentNotBootstrapped as e:
        assert "provision_dev_sp.sh" in str(e)
    except authz.AccessDenied:
        assert False, "must not surface as AccessDenied — that's a genuine-denial signal, not a bootstrap prompt"


def test_assert_can_access_denial_is_not_confused_with_bootstrap_error():
    # The inverse: a reachable, well-provisioned dev SP that simply denies the caller (normal ACL
    # denial) must raise authz.AccessDenied, NOT DevEnvironmentNotBootstrapped.
    prod = _prod_transport("prod-space", ["ana@x"])
    dev = _acl_transport({"dev-space": ["someone-else@x"]}, genie=NS(update_space=lambda *a, **k: None))
    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="overwrite",
                                  dev_space_id="dev-space", prod_client=prod, dev_client=dev)
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass
    except rehydrate.DevEnvironmentNotBootstrapped:
        assert False, "a working-but-denying dev SP must not look like a bootstrap problem"


def test_source_prod_read_error_from_workspace_error_is_access_denied_not_bootstrap():
    # assert_can_access itself fails closed on ANY ACL-read error (NotFound etc.) by raising
    # AccessDenied (per authz.py) — rehydrate must not re-wrap that as a bootstrap error either.
    def boom(request_object_type, request_object_id):
        raise NotFound("space not found")

    prod = NS(permissions=NS(get=boom))
    try:
        rehydrate.rehydrate_space(source_prod_space_id="missing-space", identity=ANA, mode="create",
                                  prod_client=prod, dev_client=NS())
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


# --- audit: acting identity + SP-broad-grant fact ---------------------------------------------


class _FakeStore:
    def __init__(self):
        self.events = []
        self.rehydrate_events = []

    def append_audit_event(self, promotion_id, event_type, **kw):
        self.events.append((promotion_id, event_type, kw))
        return NS(seq=len(self.events))

    def append_rehydrate_event(self, **kw):
        self.rehydrate_events.append(kw)
        return NS(id=f"rh-{len(self.rehydrate_events)}")


def test_audit_event_records_acting_identity_and_sp_broad_grant_fact():
    prod = _prod_transport("prod-space", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    store = _FakeStore()

    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, store=store, promotion_id="promo-1")

    assert len(store.events) == 1
    promotion_id, event_type, kw = store.events[0]
    assert promotion_id == "promo-1"
    assert event_type == "rehydrated"
    assert kw["actor_app_email"] == "ana@x"  # the live-checked ACTING identity
    detail = kw["detail"]
    assert detail["acting_identity"] == "ana@x"
    assert "dev-reader/writer service principal" in detail["sp_broad_grant"]
    assert detail["dev_space_id"] == "new-id"
    assert detail["source_prod_space_id"] == "prod-space"


def test_audit_event_type_is_registered_and_recurring():
    # A rehydrate must be able to recur (reseed dev again after another wipe) without hitting the
    # per-type milestone dedup that protects GitHub-observed events.
    assert "rehydrated" in promotion_store.EVENT_TYPES
    assert "rehydrated" in promotion_store.RECURRING_EVENTS


def test_audit_no_op_without_a_store():
    prod = _prod_transport("prod-space", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    # No store/promotion_id -> must not raise.
    result = rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                                       prod_client=prod, dev_client=dev)
    assert result.space_id == "new-id"


def test_audit_records_via_real_in_memory_backend():
    # End-to-end through the REAL PromotionStore/InMemoryBackend (not just a fake), so a schema/
    # domain-logic drift in promotion_store.py would break this test too.
    backend = promotion_store.InMemoryBackend()
    store = promotion_store.PromotionStore(backend)
    promo = store.create_promotion(
        resource_id="prod-space", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@x", branch="promote/recebiveis", current_phase="deployed",
        live_status=None, change_provider="github", external_id="1", external_url="https://x")

    prod = _prod_transport("prod-space", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, store=store, promotion_id=promo.id)

    events = store.list_audit_events(promo.id)
    assert len(events) == 1 and events[0].event_type == "rehydrated"
    assert events[0].actor_app_email == "ana@x"

    # Recurs cleanly (a second rehydrate on the same promotion is NOT deduped as a milestone).
    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, store=store, promotion_id=promo.id)
    assert len(store.list_audit_events(promo.id)) == 2


def test_no_matching_promotion_still_rehydrates_and_records_a_standalone_event():
    # Stakeholder decision (post-review of the original "requires promotion_id" gate): the prod
    # store starts EMPTY (ADR-0006), so most prod Spaces never went through this app's promotion
    # flow -> rehydrate must still work for any prod Space the caller can access. With a store
    # present and no promotion_id, the create/overwrite proceeds and gets a STANDALONE
    # rehydrate_events row (via the fake store) instead of a Promotion-linked audit_events one.
    prod = _prod_transport("prod-1", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    store = _FakeStore()

    result = rehydrate.rehydrate_space(source_prod_space_id="prod-1", identity=ANA, mode="create",
                                       prod_client=prod, dev_client=dev, store=store,
                                       promotion_id=None)

    assert result.space_id == "new-id"  # not refused -> the dev Space really got created
    assert store.events == []  # no Promotion to link -> NOT the audit_events path
    assert len(store.rehydrate_events) == 1
    kw = store.rehydrate_events[0]
    assert kw["resource_id"] == "prod-1"
    assert kw["actor_email"] == "ana@x"
    assert kw["mode"] == "create"
    assert kw["dev_space_id"] == "new-id"
    assert kw["detail"]["acting_identity"] == "ana@x"


def test_matching_promotion_still_uses_the_linked_audit_path_not_a_standalone_row():
    # The inverse of the above: when a source Promotion IS given, the richer Promotion-linked
    # `rehydrated` audit_events row is used (unchanged), and NOT the standalone rehydrate_events
    # table — the two paths are mutually exclusive per rehydrate.
    prod = _prod_transport("prod-1", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    store = _FakeStore()

    rehydrate.rehydrate_space(source_prod_space_id="prod-1", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, store=store,
                              promotion_id="promo-1")

    assert len(store.events) == 1 and store.rehydrate_events == []
    promotion_id, event_type, kw = store.events[0]
    assert promotion_id == "promo-1" and event_type == "rehydrated"


def test_standalone_event_records_via_real_in_memory_backend():
    # End-to-end through the REAL PromotionStore/InMemoryBackend (not just a fake), so a schema/
    # domain-logic drift in promotion_store.py's rehydrate_events table would break this test too.
    store = promotion_store.PromotionStore(promotion_store.InMemoryBackend())
    prod = _prod_transport("prod-1", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))

    rehydrate.rehydrate_space(source_prod_space_id="prod-1", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, store=store, promotion_id=None)

    events = store.list_rehydrate_events()
    assert len(events) == 1
    assert events[0].resource_id == "prod-1"
    assert events[0].actor_email == "ana@x"
    assert events[0].mode == "create"
    assert events[0].dev_space_id == "new-id"


# --- G6: table de-para (table_mapping) -----------------------------------------------------------


def _dev_tables_transport(tables_by_schema: dict, *, genie=None):
    """A fake dev transport exposing `.tables.list(catalog, schema)` (yields `NS(name=...)`) — for
    `preview_rehydrate`'s best-effort dev-suggestions. `genie` (if given) is attached too, so the
    same fake can double as a create/overwrite dev transport."""
    def list_tables(catalog, schema, **kw):
        for name in tables_by_schema.get((catalog, schema), []):
            yield NS(name=name)

    ns = NS(tables=NS(list=list_tables))
    if genie is not None:
        ns.genie = genie
    return ns


def test_table_mapping_overrides_the_default_target():
    prod = _prod_transport("prod-space", ["ana@x"])
    created = {}
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: (
        created.update(serialized_space=ss), NS(space_id="new-id"))[-1]))

    mapping = {"prod_recebiveis.diamond.fato_recebiveis": "dev_recebiveis.diamond.custom_name"}
    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, table_mapping=mapping)

    body = json.loads(created["serialized_space"])
    assert body["data_sources"]["tables"][0]["identifier"] == "dev_recebiveis.diamond.custom_name"
    # the benchmark SQL occurrence of the SAME ref was rewritten too, not just the identifier
    assert "dev_recebiveis.diamond.custom_name" in body["benchmarks"]["questions"][0]["answer"][0]["content"][0]


def test_table_mapping_identity_override_is_a_no_op():
    # Mapping a ref to its OWN plain default target must round-trip exactly like no mapping at all.
    prod = _prod_transport("prod-space", ["ana@x"])
    created = {}
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: (
        created.update(serialized_space=ss), NS(space_id="new-id"))[-1]))

    mapping = {"prod_recebiveis.diamond.fato_recebiveis": "dev_recebiveis.diamond.fato_recebiveis"}
    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, table_mapping=mapping)
    body = json.loads(created["serialized_space"])
    assert body["data_sources"]["tables"][0]["identifier"] == "dev_recebiveis.diamond.fato_recebiveis"


def test_table_mapping_rejects_a_target_pointing_back_at_prod():
    prod = _prod_transport("prod-space", ["ana@x"])
    dev_touched = {"value": False}

    class _PoisonedDev:
        def __getattr__(self, name):
            dev_touched["value"] = True
            raise AssertionError(f"dev transport must not be touched: {name}")

    mapping = {"prod_recebiveis.diamond.fato_recebiveis": "prod_recebiveis.diamond.fato_recebiveis"}
    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                                  prod_client=prod, dev_client=_PoisonedDev(), table_mapping=mapping)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "catálogo de produção" in str(e)
    # the refusal happens BEFORE the dev transport is ever resolved/touched — same "deny before
    # writing" discipline as the reverse-allowlist check and the overwrite-target guard.
    assert dev_touched["value"] is False


def test_table_mapping_rejects_a_malformed_target():
    prod = _prod_transport("prod-space", ["ana@x"])
    mapping = {"prod_recebiveis.diamond.fato_recebiveis": "not-a-three-part-ref"}
    try:
        rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                                  prod_client=prod, dev_client=NS(), table_mapping=mapping)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "catalog.schema.table" in str(e)


def test_apply_table_mapping_widened_allowlist_still_catches_an_unrelated_leak():
    # Exercises `_apply_table_mapping` DIRECTLY (bypassing `rehydrate_space`'s EARLIER base
    # reverse-allowlist check, which would otherwise catch this same stray leak before ever reaching
    # the mapping step — a payload with a pre-existing foreign-catalog leak never gets this far
    # through the full rehydrate_space path) — proves the widened-allowlist re-check inside
    # `_apply_table_mapping` is itself correct: a mapping retargets ONE ref to a non-default (but
    # still non-prod) catalog; a SEPARATE, stray foreign-catalog reference elsewhere is still caught.
    rebound = {
        "data_sources": {"tables": [
            {"identifier": "dev_recebiveis.diamond.fato_recebiveis"},
            {"identifier": "dev_recebiveis.diamond.dim_cedente"},
        ]},
        "instructions": {"example_question_sqls": [
            {"sql": ["SELECT * FROM sbx_recebiveis.diamond.t"]}
        ]},
    }
    mapping = {"prod_recebiveis.diamond.dim_cedente": "dev_sandbox.diamond.dim_cedente"}
    try:
        rehydrate._apply_table_mapping(rebound, mapping, domain="recebiveis")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "sbx_recebiveis" in str(e)
        assert "dev_sandbox" not in str(e)  # the CHOSEN catalog must not itself be flagged


def test_audit_detail_carries_new_title_and_table_mapping():
    prod = _prod_transport("prod-space", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    store = _FakeStore()
    mapping = {"prod_recebiveis.diamond.fato_recebiveis": "dev_recebiveis.diamond.custom_name"}

    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, title="Recebíveis (dev)",
                              store=store, promotion_id="promo-1", table_mapping=mapping)

    _, _, kw = store.events[0]
    assert kw["detail"]["new_title"] == "Recebíveis (dev)"
    assert kw["detail"]["table_mapping"] == mapping


def test_audit_detail_defaults_new_title_none_and_table_mapping_empty():
    # A plain rehydrate with no overrides at all must still carry BOTH keys (nullable/empty, not
    # missing) so an audit reviewer can tell "no override" apart from "field not even recorded".
    prod = _prod_transport("prod-space", ["ana@x"])
    dev = _acl_transport({}, genie=NS(create_space=lambda wh, ss, **kw: NS(space_id="new-id")))
    store = _FakeStore()

    rehydrate.rehydrate_space(source_prod_space_id="prod-space", identity=ANA, mode="create",
                              prod_client=prod, dev_client=dev, store=store, promotion_id="promo-1")

    _, _, kw = store.events[0]
    assert kw["detail"]["new_title"] is None
    assert kw["detail"]["table_mapping"] == {}


# --- G6: preview_rehydrate (read-only, before the caller commits) --------------------------------


def test_preview_rehydrate_returns_title_and_default_targets():
    prod = _prod_transport("prod-space", ["ana@x"])
    prod.genie.get_space = lambda sid, include_serialized_space=False: NS(
        title="Recebíveis", serialized_space=json.dumps(PROD_SERIALIZED, ensure_ascii=False))

    preview = rehydrate.preview_rehydrate("prod-space", identity=ANA, prod_client=prod,
                                          dev_client=_dev_tables_transport({}))
    assert preview["title"] == "Recebíveis"
    assert preview["tables"] == [{
        "source": "prod_recebiveis.diamond.fato_recebiveis",
        "default_target": "dev_recebiveis.diamond.fato_recebiveis",
        "dev_suggestions": [],
    }]


def test_preview_rehydrate_denies_before_export():
    prod = _prod_transport("prod-space", ["ana@x"])
    try:
        rehydrate.preview_rehydrate("prod-space", identity=MALLORY, prod_client=prod,
                                    dev_client=NS())
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_preview_rehydrate_dev_suggestions_best_effort_lists_existing_tables():
    prod = _prod_transport("prod-space", ["ana@x"])
    prod.genie.get_space = lambda sid, include_serialized_space=False: NS(
        title="Recebíveis", serialized_space=json.dumps(PROD_SERIALIZED, ensure_ascii=False))
    dev = _dev_tables_transport({("dev_recebiveis", "diamond"): ["fato_recebiveis", "fato_recebiveis_v2"]})

    preview = rehydrate.preview_rehydrate("prod-space", identity=ANA, prod_client=prod, dev_client=dev)
    assert preview["tables"][0]["dev_suggestions"] == [
        "dev_recebiveis.diamond.fato_recebiveis", "dev_recebiveis.diamond.fato_recebiveis_v2",
    ]


def test_preview_rehydrate_dev_suggestions_degrade_to_empty_on_error():
    # ANY failure listing dev tables (unreachable SP, schema not yet created, missing grant) must
    # degrade to an empty suggestion list — never raise and never block the preview itself.
    prod = _prod_transport("prod-space", ["ana@x"])
    prod.genie.get_space = lambda sid, include_serialized_space=False: NS(
        title="Recebíveis", serialized_space=json.dumps(PROD_SERIALIZED, ensure_ascii=False))

    class _BoomTables:
        def list(self, catalog, schema, **kw):
            raise RuntimeError("dev schema not found")

    preview = rehydrate.preview_rehydrate("prod-space", identity=ANA, prod_client=prod,
                                          dev_client=NS(tables=_BoomTables()))
    assert preview["tables"][0]["dev_suggestions"] == []


def test_preview_rehydrate_dev_suggestions_degrade_when_no_dev_client_and_not_bootstrapped(monkeypatch):
    # No dev_client injected -> falls through to app_logic._client(scope="dev-sp"), which raises when
    # APP_DEV_HOST isn't configured (same SP2 gap as rehydrate_space) — the preview must still
    # succeed, just with no suggestions, since a suggestion is never a hard dependency.
    prod = _prod_transport("prod-space", ["ana@x"])
    prod.genie.get_space = lambda sid, include_serialized_space=False: NS(
        title="Recebíveis", serialized_space=json.dumps(PROD_SERIALIZED, ensure_ascii=False))
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", None)

    preview = rehydrate.preview_rehydrate("prod-space", identity=ANA, prod_client=prod)
    assert preview["tables"][0]["dev_suggestions"] == []

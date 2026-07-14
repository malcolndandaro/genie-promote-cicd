"""Offline contract tests for ka_endpoints_store (S7a, app-ux-overhaul) — InMemoryBackend, NO live
Lakebase.

Mirrors tests/test_rules_store.py's conventions: asserts the store's external contract — create
validation (scope invariants), partial update, idempotent delete, `list_enabled_for_space`'s
scope-matching, and the append-only audit trail.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from ka_endpoints_store import InMemoryBackend, KaEndpointsStore  # noqa: E402


@pytest.fixture
def store():
    return KaEndpointsStore(InMemoryBackend())


# --- create: scope invariants ---------------------------------------------------------------------


def test_create_a_global_endpoint(store):
    e = store.create(name="Handbook KA", serving_endpoint_name="ka-handbook", is_global=True,
                     actor_email="admin@x")
    assert e.is_global is True and e.scope_space_ids == []
    assert store.get(e.id).name == "Handbook KA"


def test_create_a_space_scoped_endpoint(store):
    e = store.create(name="Recebíveis KA", serving_endpoint_name="ka-recebiveis",
                     scope_space_ids=["s1", "s2"], actor_email="admin@x")
    assert e.is_global is False and e.scope_space_ids == ["s1", "s2"]


def test_create_rejects_both_global_and_scoped(store):
    with pytest.raises(ValueError):
        store.create(name="x", serving_endpoint_name="ka-x", is_global=True,
                     scope_space_ids=["s1"], actor_email="admin@x")


def test_create_rejects_neither_global_nor_scoped(store):
    with pytest.raises(ValueError):
        store.create(name="x", serving_endpoint_name="ka-x", actor_email="admin@x")


def test_create_requires_name_and_serving_endpoint_and_actor(store):
    with pytest.raises(ValueError):
        store.create(name="", serving_endpoint_name="ka-x", is_global=True, actor_email="admin@x")
    with pytest.raises(ValueError):
        store.create(name="x", serving_endpoint_name="", is_global=True, actor_email="admin@x")
    with pytest.raises(ValueError):
        store.create(name="x", serving_endpoint_name="ka-x", is_global=True, actor_email="")


def test_multiple_endpoints_coexist_independently_scoped(store):
    store.create(name="Global KA", serving_endpoint_name="ka-g", is_global=True, actor_email="admin@x")
    store.create(name="Scoped KA", serving_endpoint_name="ka-s", scope_space_ids=["s1"], actor_email="admin@x")
    assert len(store.list_all()) == 2


# --- update: partial + re-validated scope ----------------------------------------------------------


def test_update_toggles_enabled_without_touching_other_fields(store):
    e = store.create(name="x", serving_endpoint_name="ka-x", is_global=True, actor_email="admin@x")
    updated = store.update(e.id, enabled=False, actor_email="admin@x")
    assert updated.enabled is False and updated.name == "x" and updated.is_global is True


def test_update_can_change_scope_from_global_to_specific_spaces(store):
    e = store.create(name="x", serving_endpoint_name="ka-x", is_global=True, actor_email="admin@x")
    updated = store.update(e.id, is_global=False, scope_space_ids=["s1"], actor_email="admin@x")
    assert updated.is_global is False and updated.scope_space_ids == ["s1"]


def test_update_rejects_a_resulting_invalid_scope(store):
    e = store.create(name="x", serving_endpoint_name="ka-x", scope_space_ids=["s1"], actor_email="admin@x")
    # Switching to global WITHOUT clearing scope_space_ids would be both — rejected.
    with pytest.raises(ValueError):
        store.update(e.id, is_global=True, actor_email="admin@x")


def test_update_unknown_id_raises(store):
    with pytest.raises(ValueError):
        store.update("nope", enabled=False, actor_email="admin@x")


# --- delete: idempotent ------------------------------------------------------------------------


def test_delete_is_idempotent(store):
    e = store.create(name="x", serving_endpoint_name="ka-x", is_global=True, actor_email="admin@x")
    store.delete(e.id, actor_email="admin@x")
    assert store.get(e.id) is None
    store.delete(e.id, actor_email="admin@x")  # no-op, no raise


# --- list_enabled_for_space: the query S7b's reviewer integration needs ------------------------


def test_list_enabled_for_space_matches_global_and_explicit_scope(store):
    global_ep = store.create(name="Global", serving_endpoint_name="ka-g", is_global=True, actor_email="admin@x")
    scoped_ep = store.create(name="Scoped", serving_endpoint_name="ka-s", scope_space_ids=["s1"], actor_email="admin@x")
    store.create(name="Other scope", serving_endpoint_name="ka-o", scope_space_ids=["s2"], actor_email="admin@x")

    for_s1 = store.list_enabled_for_space("s1")
    assert {e.id for e in for_s1} == {global_ep.id, scoped_ep.id}

    for_s3 = store.list_enabled_for_space("s3")  # only the global one applies
    assert {e.id for e in for_s3} == {global_ep.id}


def test_list_enabled_for_space_excludes_disabled_endpoints(store):
    e = store.create(name="Global", serving_endpoint_name="ka-g", is_global=True, actor_email="admin@x")
    store.update(e.id, enabled=False, actor_email="admin@x")
    assert store.list_enabled_for_space("any-space") == []


def test_list_enabled_for_space_dicts_is_dataclass_free(store):
    store.create(name="Global", serving_endpoint_name="ka-g", is_global=True, actor_email="admin@x")
    dicts = store.list_enabled_for_space_dicts("any-space")
    assert dicts == [dict(d) for d in dicts]  # plain dicts, not KaEndpoint instances
    assert dicts[0]["serving_endpoint_name"] == "ka-g"


# --- audit trail: append-only -------------------------------------------------------------------


def test_create_update_delete_all_audited(store):
    e = store.create(name="x", serving_endpoint_name="ka-x", is_global=True, actor_email="admin@x")
    store.update(e.id, enabled=False, actor_email="admin@x")
    store.delete(e.id, actor_email="admin@x")
    events = store.list_audit_events(e.id)
    assert [ev.event_type for ev in events] == ["ka_endpoint_created", "ka_endpoint_updated", "ka_endpoint_deleted"]
    assert all(ev.actor_email == "admin@x" for ev in events)


def test_delete_of_a_missing_endpoint_is_not_audited(store):
    store.delete("nope", actor_email="admin@x")
    assert store.list_audit_events("nope") == []

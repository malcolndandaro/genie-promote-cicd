"""Offline contract tests for rules_store (G2) — InMemoryBackend, NO live Lakebase.

Mirrors the other store tests: asserts the
store's external contract — upsert-by-rule_id, idempotent reset, custom-rule validation, and the
append-only audit trail (including that a reset/delete does NOT wipe the audit history that
preceded it — the one place this store's contract deliberately differs from its siblings).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from rules_store import InMemoryBackend, RulesStore  # noqa: E402


@pytest.fixture
def store():
    return RulesStore(InMemoryBackend())


# --- plain overrides of a hardcoded rule ---------------------------------------------------------


def test_upsert_a_plain_override_persists(store):
    o = store.upsert(rule_id="ENV-01", actor_email="admin@x", enabled=False)
    assert o.rule_id == "ENV-01" and o.is_custom is False and o.enabled is False
    assert store.get("ENV-01").enabled is False


def test_upsert_is_idempotent_by_rule_id(store):
    a = store.upsert(rule_id="EVAL-01", actor_email="admin@x", severity="SUGGESTION")
    b = store.upsert(rule_id="EVAL-01", actor_email="admin@x", severity="STYLE")
    assert b.created_at == a.created_at  # same row, not a duplicate
    assert store.get("EVAL-01").severity == "STYLE"
    assert len(store.list_all()) == 1


def test_upsert_rejects_unknown_severity(store):
    with pytest.raises(ValueError):
        store.upsert(rule_id="ENV-01", actor_email="admin@x", severity="CATASTROPHIC")


def test_upsert_rejects_empty_rule_id(store):
    with pytest.raises(ValueError):
        store.upsert(rule_id="  ", actor_email="admin@x")


def test_upsert_requires_actor_email(store):
    with pytest.raises(ValueError):
        store.upsert(rule_id="ENV-01", actor_email="")


# --- custom rules ---------------------------------------------------------------------------------


def test_custom_rule_requires_severity_content_and_citation(store):
    with pytest.raises(ValueError):
        store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True)
    with pytest.raises(ValueError):
        store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True, severity="BLOCKER")
    with pytest.raises(ValueError):
        store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True,
                     severity="BLOCKER", content="texto")


def test_custom_rule_persists_with_full_fields(store):
    o = store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True,
                     severity="SUGGESTION", content="nunca use SELECT *", citation="Padrão interno")
    assert o.is_custom is True and o.content == "nunca use SELECT *" and o.citation == "Padrão interno"


# --- reset / delete --------------------------------------------------------------------------------


def test_reset_removes_a_plain_override(store):
    store.upsert(rule_id="ENV-01", actor_email="admin@x", enabled=False)
    store.reset("ENV-01", actor_email="admin@x")
    assert store.get("ENV-01") is None


def test_reset_is_idempotent_when_absent(store):
    store.reset("GHOST-01", actor_email="admin@x")  # no-op, must not raise
    assert store.list_audit_events("GHOST-01") == []


def test_reset_deletes_a_custom_rule_entirely(store):
    store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True,
                severity="STYLE", content="c", citation="cit")
    store.reset("CUSTOM-01", actor_email="admin@x")
    assert store.get("CUSTOM-01") is None


# --- append-only audit trail --------------------------------------------------------------------


def test_first_override_of_a_hardcoded_rule_is_audited_as_updated(store):
    store.upsert(rule_id="ENV-01", actor_email="admin@x", enabled=False)
    events = store.list_audit_events("ENV-01")
    assert [e.event_type for e in events] == ["rule_updated"]
    assert events[0].actor_email == "admin@x"


def test_creating_a_custom_rule_is_audited_as_created_then_updated(store):
    store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True,
                severity="STYLE", content="c", citation="cit")
    store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True,
                severity="BLOCKER", content="c2", citation="cit")
    events = store.list_audit_events("CUSTOM-01")
    assert [e.event_type for e in events] == ["rule_created", "rule_updated"]


def test_resetting_a_plain_override_is_audited_as_reset_not_deleted(store):
    store.upsert(rule_id="ENV-01", actor_email="admin@x", enabled=False)
    store.reset("ENV-01", actor_email="ops@x")
    events = store.list_audit_events("ENV-01")
    assert events[-1].event_type == "rule_reset"
    assert events[-1].actor_email == "ops@x"


def test_deleting_a_custom_rule_is_audited_as_deleted(store):
    store.upsert(rule_id="CUSTOM-01", actor_email="admin@x", is_custom=True,
                severity="STYLE", content="c", citation="cit")
    store.reset("CUSTOM-01", actor_email="admin@x")
    events = store.list_audit_events("CUSTOM-01")
    assert events[-1].event_type == "rule_deleted"


def test_audit_history_survives_a_reset_who_changed_what_when(store):
    # The one place this store's contract deliberately differs from its siblings (see the module
    # docstring): the audit trail is NOT tied to the row's lifetime via a foreign key.
    store.upsert(rule_id="EVAL-01", actor_email="admin@x", params={"min_benchmarks": 3})
    store.reset("EVAL-01", actor_email="admin@x")
    events = store.list_audit_events("EVAL-01")
    assert len(events) == 2
    assert events[0].detail["params"] == {"min_benchmarks": 3}


def test_eval_run_threshold_param_round_trips_and_is_audited_for_free(store):
    # W3 follow-up: eval_run_threshold lives in the SAME opaque params jsonb as min_benchmarks —
    # the store has no rule-specific knowledge of either key, so both the CRUD round-trip and the
    # `rule_updated` audit event ("params" in `upsert`'s detail dict) cover the new key with zero
    # store code changes; this just makes that coverage explicit for the new field.
    override = store.upsert(rule_id="EVAL-01", actor_email="admin@x",
                            params={"min_benchmarks": 5, "eval_run_threshold": 0.6})
    assert override.params == {"min_benchmarks": 5, "eval_run_threshold": 0.6}

    fetched = store.get("EVAL-01")
    assert fetched.params == {"min_benchmarks": 5, "eval_run_threshold": 0.6}

    as_dict = store.list_all_dicts()[0]
    assert as_dict["params"] == {"min_benchmarks": 5, "eval_run_threshold": 0.6}

    events = store.list_audit_events("EVAL-01")
    assert events[-1].detail["params"] == {"min_benchmarks": 5, "eval_run_threshold": 0.6}


def test_list_all_dicts_returns_plain_dicts_for_the_pure_engine_boundary(store):
    store.upsert(rule_id="ENV-01", actor_email="admin@x", enabled=False)
    rows = store.list_all_dicts()
    assert rows == [dict(r) for r in rows]  # already plain dicts
    assert rows[0]["rule_id"] == "ENV-01"


def test_healthcheck_and_close_do_not_raise(store):
    store.healthcheck()
    store.close()


# --- seed_defaults: the handbook rules become real, editable rows on a fresh store ---------------

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "genie_reviewer"))
import handbook_rules  # noqa: E402
import rules_config  # noqa: E402


def test_seed_defaults_populates_an_empty_store_with_the_handbook_rules(store):
    n = store.seed_defaults(handbook_rules.RULES)
    assert n == len(handbook_rules.RULES) == 7
    rows = store.list_all()
    assert {r.rule_id for r in rows} == {r["rule_id"] for r in handbook_rules.RULES}
    # seeded as plain (non-custom) overrides, enabled, carrying the handbook's own values
    env01 = store.get("ENV-01")
    assert env01.is_custom is False and env01.enabled is True
    assert env01.severity == "BLOCKER" and env01.citation and env01.content


def test_seed_defaults_is_idempotent_and_never_clobbers(store):
    assert store.seed_defaults(handbook_rules.RULES) == 7
    # an admin edits one rule AFTER the seed
    store.upsert(rule_id="ENV-01", actor_email="admin@x", enabled=False, severity="STYLE")
    # a second seed (e.g. next boot / another replica) is a no-op — store already populated
    assert store.seed_defaults(handbook_rules.RULES) == 0
    assert len(store.list_all()) == 7
    assert store.get("ENV-01").enabled is False  # admin edit preserved, not reset by re-seed


def test_seed_defaults_does_not_emit_audit_events(store):
    # seeding is the store's INITIAL STATE, not an admin action — it must not pollute the trail.
    store.seed_defaults(handbook_rules.RULES)
    assert store.list_audit_events("ENV-01") == []


def test_effective_rules_after_seed_equals_the_hardcoded_baseline(store):
    # the whole point: seeding makes the rows real + editable WITHOUT changing what the reviewer
    # grounds on — effective_rules over the seeded overrides == the hardcoded handbook set.
    store.seed_defaults(handbook_rules.RULES)
    effective = rules_config.effective_rules(store.list_all_dicts())
    by_id = {r["rule_id"]: r for r in effective}
    assert set(by_id) == {r["rule_id"] for r in handbook_rules.RULES}
    for hardcoded in handbook_rules.RULES:
        got = by_id[hardcoded["rule_id"]]
        assert got["severity_hint"] == hardcoded["severity_hint"]
        assert got["content"] == hardcoded["content"]
        assert got["citation"] == hardcoded["citation"]

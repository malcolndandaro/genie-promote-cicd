"""Offline contract tests for prompt_template_store (S8, app-ux-overhaul) — InMemoryBackend, NO
live Lakebase.

Mirrors tests/test_rules_store.py's conventions, simplified for a SINGLETON config value (no
per-item key): current-value get/save/reset, idempotent reset, and the append-only audit trail.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from prompt_template_store import InMemoryBackend, PromptTemplateStore  # noqa: E402


@pytest.fixture
def store():
    return PromptTemplateStore(InMemoryBackend())


def test_get_returns_none_when_nothing_saved(store):
    assert store.get() is None


def test_save_persists_and_get_returns_it(store):
    saved = store.save(template_text="Você é um revisor mais rigoroso.", actor_email="admin@x")
    assert saved.template_text == "Você é um revisor mais rigoroso." and saved.updated_by == "admin@x"
    got = store.get()
    assert got.template_text == "Você é um revisor mais rigoroso."


def test_save_replaces_the_current_value_no_version_history(store):
    store.save(template_text="v1", actor_email="admin@x")
    store.save(template_text="v2", actor_email="admin@x")
    assert store.get().template_text == "v2"  # only ONE current value, no way to list v1 again


def test_save_rejects_empty_text(store):
    with pytest.raises(ValueError):
        store.save(template_text="   ", actor_email="admin@x")


def test_save_requires_actor_email(store):
    with pytest.raises(ValueError):
        store.save(template_text="x", actor_email="")


def test_reset_removes_the_custom_value(store):
    store.save(template_text="custom", actor_email="admin@x")
    store.reset(actor_email="admin@x")
    assert store.get() is None


def test_reset_is_idempotent_when_nothing_saved(store):
    store.reset(actor_email="admin@x")  # no-op, no raise
    assert store.list_audit_events() == []


# --- audit trail: append-only, before/after captured --------------------------------------------


def test_save_and_reset_are_both_audited_with_before_after(store):
    store.save(template_text="v1", actor_email="admin@x")
    store.save(template_text="v2", actor_email="admin@y")
    store.reset(actor_email="admin@x")
    events = store.list_audit_events()
    assert [e.event_type for e in events] == [
        "prompt_template_saved", "prompt_template_saved", "prompt_template_reset"]
    assert events[0].detail == {"before": None, "after": "v1"}
    assert events[1].detail == {"before": "v1", "after": "v2"}
    assert events[2].detail == {"before": "v2"}
    assert [e.actor_email for e in events] == ["admin@x", "admin@y", "admin@x"]

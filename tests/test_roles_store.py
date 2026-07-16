"""Offline contract tests for roles_store — InMemoryBackend, NO live Lakebase.

Asserts the store's external contract —
upsert-by-(email,role), idempotent revoke, the email<->GitHub-username mapping, and — the
acceptance-criteria-bearing behavior — `effective_emails`'s store-over-env precedence, including
the fail-closed "empty store AND empty env -> empty set" case.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from roles_store import InMemoryBackend, RolesStore  # noqa: E402


@pytest.fixture
def store():
    return RolesStore(InMemoryBackend())


def test_assign_persists_and_is_idempotent_upsert(store):
    a = store.assign(email="pedro@acme.com", role="steward", github_username="PSPedro176")
    assert a.email == "pedro@acme.com" and a.role == "steward" and a.github_username == "PSPedro176"
    # Re-assigning the SAME (email, role) updates in place — no duplicate row.
    b = store.assign(email="pedro@acme.com", role="steward", github_username="pedro-gh")
    assert b.id == a.id
    all_rows = store.list_all()
    assert len(all_rows) == 1 and all_rows[0].github_username == "pedro-gh"


def test_assign_normalizes_email_case(store):
    store.assign(email="Pedro@ACME.com", role="admin")
    assert store.list_all()[0].email == "pedro@acme.com"


def test_assign_rejects_unknown_role(store):
    with pytest.raises(ValueError):
        store.assign(email="a@x", role="superuser")


def test_assign_rejects_empty_email(store):
    with pytest.raises(ValueError):
        store.assign(email="  ", role="admin")


def test_same_email_can_hold_multiple_roles(store):
    store.assign(email="malcoln@acme.com", role="admin")
    store.assign(email="malcoln@acme.com", role="steward")
    roles = {r.role for r in store.list_all() if r.email == "malcoln@acme.com"}
    assert roles == {"admin", "steward"}


def test_revoke_removes_the_assignment(store):
    store.assign(email="a@x", role="steward")
    store.revoke(email="a@x", role="steward")
    assert store.list_all() == []


def test_revoke_is_idempotent_when_absent(store):
    store.revoke(email="ghost@x", role="steward")  # no-op, must not raise


def test_github_username_for_unmapped_returns_none(store):
    store.assign(email="a@x", role="steward")  # no github_username given
    assert store.github_username_for("a@x") is None


def test_github_username_for_mapped(store):
    store.assign(email="a@x", role="steward", github_username="a-gh")
    assert store.github_username_for("a@x") == "a-gh"
    assert store.github_username_for("A@X") == "a-gh"  # case-insensitive lookup


# --- store-over-env precedence (the F5 acceptance criteria) -----------------------------------


def test_effective_emails_uses_env_fallback_when_store_is_empty(store):
    assert store.effective_emails("admin", env_fallback=frozenset({"admin@x"})) == {"admin@x"}


def test_effective_emails_empty_store_and_empty_env_is_fail_closed(store):
    # The core fail-closed guarantee (A2's contract, extended to F5): NEVER "everyone".
    assert store.effective_emails("admin", env_fallback=frozenset()) == frozenset()


def test_effective_emails_store_wins_once_configured(store):
    store.assign(email="new-admin@x", role="admin")
    # Even though env still names a DIFFERENT admin, the store (now non-empty) is authoritative.
    assert store.effective_emails("admin", env_fallback=frozenset({"old-admin@x"})) == {"new-admin@x"}


def test_effective_emails_ignores_env_for_a_role_with_zero_store_rows(store):
    # Once the store holds ANY row (any role), env is ignored for EVERY role — configuring a
    # Steward in-app but leaving Admins unconfigured must yield an EMPTY admin set, not a silent
    # env fallback ("no false sense of control", applied to precedence itself).
    store.assign(email="steward@x", role="steward")
    assert store.effective_emails("admin", env_fallback=frozenset({"env-admin@x"})) == frozenset()


def test_effective_emails_scoped_to_the_requested_role(store):
    store.assign(email="s@x", role="steward")
    store.assign(email="a@x", role="admin")
    assert store.effective_emails("steward", env_fallback=frozenset()) == {"s@x"}
    assert store.effective_emails("admin", env_fallback=frozenset()) == {"a@x"}


def test_healthcheck_and_close_do_not_raise(store):
    store.healthcheck()
    store.close()


def test_revoke_admin_ok_when_a_replacement_admin_remains(store):
    store.assign(email="pedro@acme.com", role="steward")
    store.assign(email="malcoln@acme.com", role="admin")
    store.revoke(email="pedro@acme.com", role="steward")  # malcoln (admin) still gates -> allowed
    assert [(r.email, r.role) for r in store.list_all()] == [("malcoln@acme.com", "admin")]


def test_revoke_down_to_empty_store_is_allowed_env_fallback_applies(store):
    # Revoking the very last row leaves a COMPLETELY empty store -> env fallback takes over, so this
    # is NOT a lockout and must be permitted (distinct from the non-empty case above).
    store.assign(email="solo@acme.com", role="admin")
    store.revoke(email="solo@acme.com", role="admin")
    assert store.list_all() == []
    # empty store -> env fallback is authoritative again
    assert store.effective_emails("admin", env_fallback=frozenset({"boot@acme.com"})) == frozenset({"boot@acme.com"})


def test_migrate_removes_demo_approver_rows_idempotently():
    backend = InMemoryBackend()
    backend.upsert({
        "id": "legacy", "email": "ana@acme.com", "role": "approver",
        "github_username": None, "created_at": None, "updated_at": None,
    })
    store = RolesStore(backend)
    store.migrate()
    store.migrate()
    assert store.list_all() == []

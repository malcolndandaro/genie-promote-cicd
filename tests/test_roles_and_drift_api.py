"""Unit tests for the R1 role/GitHub-mirror endpoints (engine_api/main.py).

R1 changes:
  - Retire "steward" as an app role; only "admin" remains.
  - `/whoami` no longer returns `is_steward` or `steward`.
  - `scope=steward-queue` on `/promotions` is now cross-user and ungated (no role check).
  - `/admin/drift` and `/promotions/{id}/drift` are removed; replaced by `/admin/github-roles`.
  - `bootstrap_env` in `GET /admin/roles` now only has `admins`.

FastAPI TestClient + fakes — no network, no live workspace, no live GitHub.

Mirrors tests/test_admin_console_api.py's conventions exactly (`_verify_as`, the admin-gating
parametrized checks, the InMemoryBackend store fixtures wired into `engine_api.app.state`).

Verifies:
  1. Roles CRUD is admin-gated on the VERIFIED identity (never a spoofed header).
  2. `whoami`/`_is_admin` reads the STORE once it holds any rows, with env as bootstrap fallback.
  3. `scope=steward-queue` is ungated and cross-user (R1 change).
  4. `GET /admin/github-roles` returns write collaborators + env reviewers with graceful degradation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import authz  # noqa: E402
import main as engine_api  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from promotion_store import InMemoryBackend as PromotionInMemoryBackend  # noqa: E402
from promotion_store import PromotionStore  # noqa: E402
from roles_store import InMemoryBackend as RolesInMemoryBackend  # noqa: E402
from roles_store import RolesStore  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


@pytest.fixture()
def roles_store_fixture():
    s = RolesStore(RolesInMemoryBackend())
    engine_api.app.state.roles_store = s
    yield s
    engine_api.app.state.roles_store = None


@pytest.fixture()
def promo_store():
    s = PromotionStore(PromotionInMemoryBackend())
    engine_api.app.state.store = s
    yield s
    engine_api.app.state.store = None


ADMIN_HEADERS = {"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"}
RANDO_HEADERS = {"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "rando@x"}


def _setup_admin(monkeypatch):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x", "tok-ana": "ana@x"})


# --- roles CRUD: admin-gated, server-side, via the VERIFIED identity ---------------------------


def test_list_roles_403_a_non_admin(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/roles", headers=RANDO_HEADERS).status_code == 403


def test_list_roles_403_with_no_token(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/roles").status_code == 403


def test_list_roles_ignores_spoofed_display_header(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.get("/api/admin/roles",
                   headers={"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "admin@x"})
    assert r.status_code == 403


def test_assign_role_403_a_non_admin(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles", json={"email": "x@y", "role": "admin"},
                    headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_revoke_role_403_a_non_admin(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles/revoke", json={"email": "x@y", "role": "admin"},
                    headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_admin_can_assign_list_and_revoke_a_role(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    # Seed the CALLER as a store-backed admin first — once the store holds ANY role row, it is
    # authoritative (env is ignored), so admin@x must have its OWN row to keep calling admin-gated
    # endpoints through the rest of this test.
    roles_store_fixture.assign(email="admin@x", role="admin")
    r = client.post("/api/admin/roles",
                    json={"email": "pedro@x", "role": "admin", "github_username": "pedro-gh"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200
    role = r.json()["role"]
    assert role["email"] == "pedro@x" and role["role"] == "admin" and role["github_username"] == "pedro-gh"

    listed = client.get("/api/admin/roles", headers=ADMIN_HEADERS).json()
    emails = {row["email"] for row in listed["roles"]}
    assert emails == {"admin@x", "pedro@x"}
    assert "bootstrap_env" in listed  # so the UI can explain WHY a fallback role is in effect
    assert "stewards" not in listed["bootstrap_env"]  # R1: stewards key removed

    client.post("/api/admin/roles/revoke", json={"email": "pedro@x", "role": "admin"},
               headers=ADMIN_HEADERS)
    remaining = client.get("/api/admin/roles", headers=ADMIN_HEADERS).json()["roles"]
    assert [row["email"] for row in remaining] == ["admin@x"]


def test_assign_role_rejects_unknown_role(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles", json={"email": "x@y", "role": "superuser"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_assign_role_rejects_retired_steward_role(monkeypatch, roles_store_fixture):
    # R1: "steward" is retired and must be rejected by the store.
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles", json={"email": "x@y", "role": "steward"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_revoke_role_is_idempotent_when_absent(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles/revoke", json={"email": "ghost@x", "role": "admin"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200


def test_roles_endpoints_require_the_store(monkeypatch):
    _setup_admin(monkeypatch)
    engine_api.app.state.roles_store = None
    assert client.get("/api/admin/roles", headers=ADMIN_HEADERS).status_code == 503


# --- whoami / _is_admin: store-over-env precedence, fail-closed --------------------------------


def test_is_admin_falls_back_to_env_when_store_empty(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)  # APP_ADMINS=admin@x, empty store
    r = client.get("/api/whoami", headers=ADMIN_HEADERS)
    assert r.json()["is_admin"] is True


def test_is_admin_reads_the_store_once_configured_no_redeploy_needed(monkeypatch, roles_store_fixture):
    # APP_ADMINS names a DIFFERENT admin; once the store holds a role, it is authoritative — the
    # acceptance criterion "changing an admin needs no redeploy".
    monkeypatch.setenv("APP_ADMINS", "old-admin@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-new": "new-admin@x", "tok-old": "old-admin@x"})
    roles_store_fixture.assign(email="new-admin@x", role="admin")

    new_admin = client.get("/api/whoami",
                           headers={"x-forwarded-access-token": "tok-new", "x-forwarded-email": "new-admin@x"})
    assert new_admin.json()["is_admin"] is True
    old_admin = client.get("/api/whoami",
                           headers={"x-forwarded-access-token": "tok-old", "x-forwarded-email": "old-admin@x"})
    assert old_admin.json()["is_admin"] is False  # env is IGNORED once the store is non-empty


def test_is_admin_fail_closed_when_store_and_env_both_empty(monkeypatch, roles_store_fixture):
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-a": "a@x"})
    r = client.get("/api/whoami", headers={"x-forwarded-access-token": "tok-a", "x-forwarded-email": "a@x"})
    assert r.json()["is_admin"] is False  # never "everyone" — fail closed


def test_whoami_has_no_steward_fields(monkeypatch, roles_store_fixture):
    # R1: is_steward and steward are removed from whoami.
    monkeypatch.delenv("APP_STEWARD", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_ADMINS", raising=False)
    r = client.get("/api/whoami")
    body = r.json()
    assert "is_steward" not in body
    assert "steward" not in body


def test_role_gated_endpoints_still_work_when_roles_store_absent(monkeypatch):
    # No roles_store fixture at all (app.state.roles_store stays None, as at import time) — the
    # env-only path (pre-F5 behavior) must be unaffected.
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    engine_api.app.state.roles_store = None
    _verify_as(monkeypatch, {"tok-admin": "admin@x"})
    r = client.get("/api/whoami", headers={"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"})
    assert r.json()["is_admin"] is True


# --- scope=steward-queue: R1 — ungated, cross-user, phase-filtered ----------------------------


def _mk_promotion(store, *, external_id, phase):
    return store.create_promotion(
        resource_id="s1", resource_kind="genie_space", resource_title="Test",
        requester_email="ana@x", branch="promote/x", current_phase=phase,
        live_status={"phase": phase}, change_provider="github", external_id=str(external_id),
        external_url=f"https://gh/{external_id}",
        audience_spec={"principals": [{"principal": "users", "is_group": True}]})


def test_steward_queue_is_ungated_in_r1(monkeypatch, promo_store):
    # R1: scope=steward-queue is accessible to ANY authenticated caller (no role check).
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-rando": "rando@x"})
    _mk_promotion(promo_store, external_id=30, phase="open")

    r = client.get("/api/promotions?scope=steward-queue",
                   headers={"x-forwarded-access-token": "tok-rando"})
    assert r.status_code == 200


def test_steward_queue_filters_to_active_phases(monkeypatch, promo_store):
    _verify_as(monkeypatch, {"tok-rando": "rando@x"})
    _mk_promotion(promo_store, external_id=31, phase="open")
    _mk_promotion(promo_store, external_id=32, phase="checks_running")
    _mk_promotion(promo_store, external_id=33, phase="awaiting_approval")
    _mk_promotion(promo_store, external_id=34, phase="checks_failed")
    _mk_promotion(promo_store, external_id=35, phase="deploying")
    _mk_promotion(promo_store, external_id=36, phase="deployed")

    r = client.get("/api/promotions?scope=steward-queue",
                   headers={"x-forwarded-access-token": "tok-rando"})
    assert r.status_code == 200
    assert {p["external_id"] for p in r.json()["promotions"]} == {"31", "32", "33"}


def test_steward_queue_is_cross_user(monkeypatch, promo_store):
    _verify_as(monkeypatch, {"tok-viewer": "viewer@x"})
    promo_store.create_promotion(
        resource_id="s1", resource_kind="genie_space", resource_title="T",
        requester_email="ana@x", branch="p/x", current_phase="open",
        live_status=None, change_provider="github", external_id="38",
        external_url="https://gh/38",
        audience_spec={"principals": [{"principal": "users", "is_group": True}]})
    promo_store.create_promotion(
        resource_id="s2", resource_kind="genie_space", resource_title="T2",
        requester_email="bob@x", branch="p/y", current_phase="open",
        live_status=None, change_provider="github", external_id="39",
        external_url="https://gh/39",
        audience_spec={"principals": [{"principal": "users", "is_group": True}]})

    r = client.get("/api/promotions?scope=steward-queue",
                   headers={"x-forwarded-access-token": "tok-viewer"})
    # viewer@x is NOT the requester of either — but the queue is cross-user so they see both.
    assert {p["external_id"] for p in r.json()["promotions"]} == {"38", "39"}


# --- GET /admin/github-roles: read-only GitHub mirror (replaces drift endpoints) ---------------


class _FakeBot:
    def __init__(self, *, write_collab=None, write_collab_error=None,
                 env_reviewers=None, env_error=None):
        self._write_collab = write_collab
        self._write_collab_error = write_collab_error
        self._env_reviewers = env_reviewers
        self._env_error = env_error

    def list_write_collaborators(self) -> list[str]:
        if self._write_collab_error:
            raise self._write_collab_error
        return self._write_collab or []

    def get_environment_reviewers(self, environment: str) -> list[str]:
        if self._env_error:
            raise self._env_error
        return self._env_reviewers or []


def test_github_roles_403_a_non_admin(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/github-roles", headers=RANDO_HEADERS).status_code == 403


def test_github_roles_returns_collaborators_and_reviewers(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeBot(
                            write_collab=["alice", "bob"],
                            env_reviewers=["charlie"]))
    r = client.get("/api/admin/github-roles", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["write_collaborators"] == ["alice", "bob"]
    assert body["environment_reviewers"] == ["charlie"]
    assert "error" not in body


def test_github_roles_degrades_gracefully_when_collaborators_fail(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeBot(
                            write_collab_error=RuntimeError("403"),
                            env_reviewers=["charlie"]))
    r = client.get("/api/admin/github-roles", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "write_collaborators_error" in body
    assert body["environment_reviewers"] == ["charlie"]


def test_github_roles_degrades_gracefully_when_env_reviewers_fail(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeBot(
                            write_collab=["alice"],
                            env_error=RuntimeError("scope missing")))
    r = client.get("/api/admin/github-roles", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["write_collaborators"] == ["alice"]
    assert "environment_reviewers_error" in body


def test_github_roles_degrades_gracefully_when_bot_cannot_be_built(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("secret scope unreachable")

    monkeypatch.setattr(engine_api.app_logic, "github_app_factory", boom)
    r = client.get("/api/admin/github-roles", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "error" in body  # top-level error key when the bot can't even be built


def test_github_roles_drift_endpoints_removed(monkeypatch, roles_store_fixture):
    # R1: /admin/drift and /promotions/{id}/drift must no longer exist.
    _setup_admin(monkeypatch)
    r = client.get("/api/admin/drift", headers=ADMIN_HEADERS)
    assert r.status_code == 404
    r2 = client.get("/api/promotions/any-id/drift", headers=ADMIN_HEADERS)
    assert r2.status_code == 404

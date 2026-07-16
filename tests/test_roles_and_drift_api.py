"""Unit tests for the F5 Phase 1 endpoints (engine_api/main.py): configurable roles (Lakebase-
backed, store-over-env precedence) + READ-ONLY GitHub drift detection. FastAPI TestClient + fakes
— no network, no live workspace, no live GitHub.

Mirrors tests/test_admin_console_api.py's conventions exactly (`_verify_as`, the admin-gating
parametrized checks, the InMemoryBackend store fixtures wired into `engine_api.app.state`).

Verifies: (1) roles CRUD is admin-gated on the VERIFIED identity (never a spoofed header), (2)
`whoami`/`_is_admin` read the STORE once it holds any rows, with env as the bootstrap fallback
(store-over-env precedence + the fail-closed empty-store-empty-env case), (3) the drift endpoints
call the injected GitHub reader and surface each divergence class, degrading to "unknown" (never
"no drift") on a GitHub read failure, and (4) the promotion-review drift endpoint is visible to the
promotion's owner (not just an admin), matching the existing per-promotion visibility boundary.
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
    r = client.post("/api/admin/roles", json={"email": "x@y", "role": "steward"},
                    headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_revoke_role_403_a_non_admin(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles/revoke", json={"email": "x@y", "role": "steward"},
                    headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_admin_can_assign_list_and_revoke_a_role(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    # Seed the CALLER as a store-backed admin first — once the store holds ANY role row, it is
    # authoritative (env is ignored), so admin@x must have its OWN row to keep calling admin-gated
    # endpoints through the rest of this test (this is the F5 precedence behavior under test
    # elsewhere; here it's just test setup).
    roles_store_fixture.assign(email="admin@x", role="admin")
    r = client.post("/api/admin/roles",
                    json={"email": "pedro@x", "role": "steward", "github_username": "pedro-gh"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200
    role = r.json()["role"]
    assert role["email"] == "pedro@x" and role["role"] == "steward" and role["github_username"] == "pedro-gh"

    listed = client.get("/api/admin/roles", headers=ADMIN_HEADERS).json()
    emails = {row["email"] for row in listed["roles"]}
    assert emails == {"admin@x", "pedro@x"}
    assert "bootstrap_env" in listed  # so the UI can explain WHY a fallback role is in effect

    client.post("/api/admin/roles/revoke", json={"email": "pedro@x", "role": "steward"},
               headers=ADMIN_HEADERS)
    remaining = client.get("/api/admin/roles", headers=ADMIN_HEADERS).json()["roles"]
    assert [row["email"] for row in remaining] == ["admin@x"]


def test_assign_role_rejects_unknown_role(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles", json={"email": "x@y", "role": "superuser"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_revoke_role_is_idempotent_when_absent(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/roles/revoke", json={"email": "ghost@x", "role": "steward"},
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


def test_whoami_steward_reads_store_when_configured(monkeypatch, roles_store_fixture):
    monkeypatch.delenv("APP_STEWARD", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_ADMINS", raising=False)
    roles_store_fixture.assign(email="new-steward@x", role="steward")
    r = client.get("/api/whoami")
    assert r.json()["steward"] == "new-steward@x"


def test_whoami_steward_falls_back_to_env_when_store_empty(monkeypatch, roles_store_fixture):
    monkeypatch.setenv("APP_STEWARD", "legacy-steward@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_ADMINS", raising=False)
    r = client.get("/api/whoami")
    assert r.json()["steward"] == "legacy-steward@x"


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


# --- whoami / is_steward: three-actor pilot capability model ------------------------------------


def test_is_steward_falls_back_to_env_when_store_empty(monkeypatch, roles_store_fixture):
    monkeypatch.setenv("APP_STEWARDS", "steward@x")
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-steward": "steward@x"})
    r = client.get("/api/whoami",
                   headers={"x-forwarded-access-token": "tok-steward", "x-forwarded-email": "steward@x"})
    assert r.json()["is_steward"] is True


def test_is_steward_reads_the_store_once_configured(monkeypatch, roles_store_fixture):
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    monkeypatch.delenv("APP_ADMINS", raising=False)
    _verify_as(monkeypatch, {"tok-new": "new-steward@x", "tok-old": "old-steward@x"})
    roles_store_fixture.assign(email="new-steward@x", role="steward")

    new = client.get("/api/whoami",
                      headers={"x-forwarded-access-token": "tok-new", "x-forwarded-email": "new-steward@x"})
    assert new.json()["is_steward"] is True
    old = client.get("/api/whoami",
                      headers={"x-forwarded-access-token": "tok-old", "x-forwarded-email": "old-steward@x"})
    assert old.json()["is_steward"] is False  # env is not even set here — store is authoritative


def test_is_steward_fails_closed_and_whoami_has_no_retired_approver_flag(monkeypatch, roles_store_fixture):
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    monkeypatch.delenv("APP_ADMINS", raising=False)
    _verify_as(monkeypatch, {"tok-a": "a@x"})
    r = client.get("/api/whoami", headers={"x-forwarded-access-token": "tok-a", "x-forwarded-email": "a@x"})
    assert r.json()["is_steward"] is False
    assert "is_approver" not in r.json()


def test_a_caller_can_hold_multiple_personas_at_once(monkeypatch, roles_store_fixture):
    # An Admin can also be the Steward; both capabilities are reflected without a third role.
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-multi": "multi@x"})
    roles_store_fixture.assign(email="multi@x", role="admin")
    roles_store_fixture.assign(email="multi@x", role="steward")
    r = client.get("/api/whoami",
                   headers={"x-forwarded-access-token": "tok-multi", "x-forwarded-email": "multi@x"})
    body = r.json()
    assert body["is_admin"] is True
    assert body["is_steward"] is True


# --- GitHub drift detection: read-only, graceful degradation ------------------------------------


class _FakeReader:
    def __init__(self, *, env_reviewers=None, env_error=None, protection=None, protection_error=None):
        self._env_reviewers, self._env_error = env_reviewers, env_error
        self._protection, self._protection_error = protection, protection_error

    def get_environment_reviewers(self, environment):
        if self._env_error:
            raise self._env_error
        return self._env_reviewers or []

    def get_branch_protection(self, branch):
        if self._protection_error:
            raise self._protection_error
        return self._protection


_CLEAN_PROTECTION = {"required_pull_request_reviews": {"required_approving_review_count": 1}}


def test_admin_drift_403_a_non_admin(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/drift", headers=RANDO_HEADERS).status_code == 403


def test_admin_drift_reports_no_drift_when_aligned(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    roles_store_fixture.assign(email="admin@x", role="steward", github_username="admin-gh")
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeReader(env_reviewers=["admin-gh"], protection=_CLEAN_PROTECTION))
    r = client.get("/api/admin/drift", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["has_drift"] is False and body["has_unknown"] is False and body["findings"] == []


def test_admin_drift_flags_unmapped_steward(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    roles_store_fixture.assign(email="admin@x", role="steward")  # no github_username
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeReader(env_reviewers=[], protection=_CLEAN_PROTECTION))
    body = client.get("/api/admin/drift", headers=ADMIN_HEADERS).json()
    assert body["has_drift"] is True
    kinds = {f["kind"] for f in body["findings"]}
    assert "steward_unmapped" in kinds


def test_admin_drift_degrades_gracefully_on_github_error_never_no_drift(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)
    roles_store_fixture.assign(email="admin@x", role="steward", github_username="admin-gh")
    monkeypatch.setattr(
        engine_api.app_logic, "github_app_factory",
        lambda *a, **k: _FakeReader(env_error=RuntimeError("403 missing scope"), protection=_CLEAN_PROTECTION))
    r = client.get("/api/admin/drift", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["has_unknown"] is True
    kinds = {f["kind"] for f in body["findings"]}
    assert "unknown_environment" in kinds
    assert "steward_not_env_reviewer" not in kinds  # never asserted — the read failed


def test_admin_drift_degrades_gracefully_when_bot_client_cannot_be_built(monkeypatch, roles_store_fixture):
    _setup_admin(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("secret scope unreachable")

    monkeypatch.setattr(engine_api.app_logic, "github_app_factory", boom)
    r = client.get("/api/admin/drift", headers=ADMIN_HEADERS)
    assert r.status_code == 200  # never crashes the caller
    body = r.json()
    assert body["has_unknown"] is True


def test_admin_drift_never_raises_even_without_a_roles_store(monkeypatch):
    # roles_store absent entirely (env-only deployment) — drift check must still run (using the env
    # fallback for stewards/admins) rather than 500ing.
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    engine_api.app.state.roles_store = None
    _verify_as(monkeypatch, {"tok-admin": "admin@x"})
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeReader(env_reviewers=[], protection=_CLEAN_PROTECTION))
    r = client.get("/api/admin/drift", headers=ADMIN_HEADERS)
    assert r.status_code == 200


# --- Contextual drift on the promotion-review screen (US-35) ------------------------------------


def _promote(monkeypatch, *, space_id, number, requester_headers):
    def fake(space_id_, *a, **k):
        return {"review": {"gate": {"conclusion": "success", "summary": "ok"}, "findings": [],
                           "eval": {"status": "pass", "summary": "n/a"}, "timeline": []},
                "pr": {"number": number, "url": f"https://gh/pr/{number}"}}

    monkeypatch.setattr(engine_api.app_logic, "request_promotion", fake)
    r = client.post("/api/promote", json={"space_id": space_id}, headers=requester_headers)
    assert r.status_code == 200
    return r.json()["promotion_id"]


def test_promotion_drift_visible_to_the_owner_not_only_admin(monkeypatch, roles_store_fixture, promo_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x", "tok-ana": "ana@x"})
    pid = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers)
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: _FakeReader(env_reviewers=[], protection=_CLEAN_PROTECTION))

    r = client.get(f"/api/promotions/{pid}/drift", headers=ana_headers)
    assert r.status_code == 200


def test_promotion_drift_403_for_an_unrelated_user(monkeypatch, roles_store_fixture, promo_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x", "tok-ana": "ana@x"})
    pid = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers)

    r = client.get(f"/api/promotions/{pid}/drift", headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_promotion_drift_404_for_unknown_promotion(monkeypatch, roles_store_fixture, promo_store):
    _setup_admin(monkeypatch)
    r = client.get("/api/promotions/does-not-exist/drift", headers=ADMIN_HEADERS)
    assert r.status_code == 404

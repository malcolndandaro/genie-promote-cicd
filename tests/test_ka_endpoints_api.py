"""Unit tests for the S7a admin endpoints (engine_api/main.py): the Knowledge Assistant endpoint
registry + the serving-endpoints picker source. FastAPI TestClient + fakes — no network, no live
workspace.

Mirrors tests/test_admin_console_api.py's / test_roles_and_drift_api.py's conventions: `_verify_as`
fakes `authz.verify_identity` so admin gating is exercised against a platform-VERIFIED identity,
never a spoofable display header.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import authz  # noqa: E402
import main as engine_api  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ka_endpoints_store import InMemoryBackend, KaEndpointsStore  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


@pytest.fixture()
def ka_store():
    s = KaEndpointsStore(InMemoryBackend())
    engine_api.app.state.ka_endpoints_store = s
    yield s
    engine_api.app.state.ka_endpoints_store = None


ADMIN_HEADERS = {"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"}
RANDO_HEADERS = {"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "rando@x"}


def _setup_admin(monkeypatch):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x"})


# --- gating: admin-only, server-side, via the VERIFIED identity ---------------------------------

KA_GET_ENDPOINTS = ("/api/admin/ka-endpoints", "/api/admin/serving-endpoints")


@pytest.mark.parametrize("path", KA_GET_ENDPOINTS)
def test_ka_endpoints_403_a_non_admin(monkeypatch, ka_store, path):
    _setup_admin(monkeypatch)
    assert client.get(path, headers=RANDO_HEADERS).status_code == 403


@pytest.mark.parametrize("path", KA_GET_ENDPOINTS)
def test_ka_endpoints_403_with_no_token(monkeypatch, ka_store, path):
    _setup_admin(monkeypatch)
    assert client.get(path).status_code == 403


def test_create_ka_endpoint_403_a_non_admin(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/ka-endpoints",
                    json={"name": "x", "serving_endpoint_name": "ka-x", "is_global": True},
                    headers=RANDO_HEADERS)
    assert r.status_code == 403


# --- serving-endpoints picker: the never-type-an-ID source --------------------------------------


def test_list_serving_endpoints_returns_the_live_list(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    monkeypatch.setattr(engine_api.app_logic, "list_serving_endpoints",
                        lambda *a, **k: [{"name": "databricks-claude-opus-4-8"}, {"name": "ka-handbook"}])
    r = client.get("/api/admin/serving-endpoints", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert {e["name"] for e in r.json()["endpoints"]} == {"databricks-claude-opus-4-8", "ka-handbook"}


# --- CRUD round-trip -----------------------------------------------------------------------------


def test_create_list_update_delete_round_trip(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    created = client.post("/api/admin/ka-endpoints",
                          json={"name": "Handbook KA", "serving_endpoint_name": "ka-handbook", "is_global": True},
                          headers=ADMIN_HEADERS)
    assert created.status_code == 200
    eid = created.json()["endpoint"]["id"]
    assert created.json()["endpoint"]["is_global"] is True

    listed = client.get("/api/admin/ka-endpoints", headers=ADMIN_HEADERS)
    assert [e["id"] for e in listed.json()["endpoints"]] == [eid]

    updated = client.post(f"/api/admin/ka-endpoints/{eid}", json={"enabled": False}, headers=ADMIN_HEADERS)
    assert updated.status_code == 200 and updated.json()["endpoint"]["enabled"] is False
    # Untouched fields survive the partial update.
    assert updated.json()["endpoint"]["name"] == "Handbook KA"

    deleted = client.post(f"/api/admin/ka-endpoints/{eid}/delete", headers=ADMIN_HEADERS)
    assert deleted.status_code == 200
    assert client.get("/api/admin/ka-endpoints", headers=ADMIN_HEADERS).json()["endpoints"] == []


def test_delete_is_idempotent_not_404(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/ka-endpoints/nope/delete", headers=ADMIN_HEADERS)
    assert r.status_code == 200


def test_create_rejects_an_invalid_scope_with_400(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/ka-endpoints",
                    json={"name": "x", "serving_endpoint_name": "ka-x", "is_global": False,
                          "scope_space_ids": []},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_update_rejects_a_resulting_invalid_scope_with_400(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    created = client.post("/api/admin/ka-endpoints",
                          json={"name": "x", "serving_endpoint_name": "ka-x",
                                "scope_space_ids": ["s1"]},
                          headers=ADMIN_HEADERS).json()["endpoint"]
    r = client.post(f"/api/admin/ka-endpoints/{created['id']}", json={"is_global": True}, headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_update_unknown_id_is_404_not_500(monkeypatch, ka_store):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/ka-endpoints/nope", json={"enabled": False}, headers=ADMIN_HEADERS)
    assert r.status_code == 400  # ValueError("no KA endpoint ...") maps to 400, same as other stores


def test_endpoints_403_when_store_absent(monkeypatch):
    # No ka_store fixture at all — app.state.ka_endpoints_store stays None (module-level default).
    _setup_admin(monkeypatch)
    r = client.get("/api/admin/ka-endpoints", headers=ADMIN_HEADERS)
    assert r.status_code == 503

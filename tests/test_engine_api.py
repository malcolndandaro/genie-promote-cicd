"""Unit tests for the engine API (AK1). FastAPI TestClient + a mocked engine — no network.

Verifies the transport + OBO plumbing: the user token is resolved from either header source
and threaded to the engine as `user_token`; the missing-token path is a 401.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import main as engine_api  # engine_api/main.py  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(engine_api.app)


def test_health_no_auth():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_whoami_reflects_forwarded_identity():
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x",
                                        "x-forwarded-user": "u-1"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "user": "u-1"}


def test_spaces_threads_proxy_header_token(monkeypatch):
    captured = {}

    def fake_list_spaces(profile=None, *, client=None, user_token=None):
        captured["user_token"] = user_token
        return [{"space_id": "abc", "title": "Recebíveis"}]

    monkeypatch.setattr(engine_api.app_logic, "list_spaces", fake_list_spaces)
    r = client.get("/spaces", headers={"x-forwarded-access-token": "tok-proxy"})
    assert r.status_code == 200
    assert r.json() == {"spaces": [{"space_id": "abc", "title": "Recebíveis"}]}
    assert captured["user_token"] == "tok-proxy"


def test_spaces_threads_bearer_token(monkeypatch):
    captured = {}

    def fake_list_spaces(profile=None, *, client=None, user_token=None):
        captured["user_token"] = user_token
        return []

    monkeypatch.setattr(engine_api.app_logic, "list_spaces", fake_list_spaces)
    r = client.get("/spaces", headers={"authorization": "Bearer tok-bearer"})
    assert r.status_code == 200
    assert captured["user_token"] == "tok-bearer"


def test_spaces_proxy_header_wins_over_bearer(monkeypatch):
    captured = {}
    monkeypatch.setattr(engine_api.app_logic, "list_spaces",
                        lambda profile=None, *, client=None, user_token=None:
                        captured.update(user_token=user_token) or [])
    client.get("/spaces", headers={"x-forwarded-access-token": "from-proxy",
                                   "authorization": "Bearer from-bearer"})
    assert captured["user_token"] == "from-proxy"


def test_spaces_requires_a_token():
    r = client.get("/spaces")
    assert r.status_code == 401


def test_spaces_rejects_authorization_without_bearer_scheme():
    r = client.get("/spaces", headers={"authorization": "tok-no-scheme"})
    assert r.status_code == 401


def test_spaces_rejects_empty_bearer():
    r = client.get("/spaces", headers={"authorization": "Bearer "})
    assert r.status_code == 401


def test_spaces_engine_error_maps_to_502(monkeypatch):
    def boom(profile=None, *, client=None, user_token=None):
        raise RuntimeError("simulated SDK auth failure")

    monkeypatch.setattr(engine_api.app_logic, "list_spaces", boom)
    r = client.get("/spaces", headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 502
    # generic body — no internal/SDK detail leaked to the client
    assert "simulated SDK auth failure" not in r.text


# --- POST /review (AK3) ---

_FAKE_REVIEW = {
    "findings": [{"rule_id": "EVAL-01", "severity": "BLOCKER", "message": "...", "citation": "..."}],
    "gate": {"conclusion": "failure", "blocker_count": 1, "summary": "🔴 ..."},
    "eval": {"status": "advisory", "summary": "🟡 ..."},
    "timeline": [{"key": "checks", "label": "...", "status": "pass"}],
    "allowlist_violations": [],
    "consumer_group": "account users",
    "prod_serialized": {"huge": "payload", "should": "not be returned"},
}


def test_review_threads_token_and_returns_ui_fields(monkeypatch):
    captured = {}

    def fake_review_space(space_id, *args, user_token=None, **kwargs):
        captured["space_id"] = space_id
        captured["user_token"] = user_token
        return dict(_FAKE_REVIEW)

    monkeypatch.setattr(engine_api.app_logic, "review_space", fake_review_space)
    r = client.post("/review", json={"space_id": "01f16e83"},
                    headers={"x-forwarded-access-token": "tok-r"})
    assert r.status_code == 200
    body = r.json()
    assert captured == {"space_id": "01f16e83", "user_token": "tok-r"}
    assert set(body.keys()) == {"findings", "gate", "eval", "timeline",
                                "allowlist_violations", "consumer_group"}
    assert "prod_serialized" not in body  # large payload dropped
    assert body["gate"]["conclusion"] == "failure"


def test_review_requires_a_token():
    r = client.post("/review", json={"space_id": "x"})
    assert r.status_code == 401


def test_review_requires_space_id():
    r = client.post("/review", json={}, headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 422  # FastAPI validation: space_id is required


def test_review_engine_error_maps_to_502(monkeypatch):
    def boom(space_id, *args, user_token=None, **kwargs):
        raise RuntimeError("simulated review failure")

    monkeypatch.setattr(engine_api.app_logic, "review_space", boom)
    r = client.post("/review", json={"space_id": "x"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 502
    assert "simulated review failure" not in r.text


def test_review_contract_drift_maps_to_502(monkeypatch):
    # If the engine ever drops a UI field, the endpoint must fail loudly (502), not emit null.
    incomplete = {k: v for k, v in _FAKE_REVIEW.items() if k != "gate"}

    monkeypatch.setattr(engine_api.app_logic, "review_space",
                        lambda space_id, *a, user_token=None, **k: dict(incomplete))
    r = client.post("/review", json={"space_id": "x"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 502

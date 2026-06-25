"""Unit tests for the single-app FastAPI engine (AK1 + SV1). FastAPI TestClient + a mocked engine
— no network.

Verifies (1) the transport + OBO plumbing: the user token is resolved from either header source
and threaded to the engine as `user_token`; the missing-token path is a 401; (2) the API is
served at BOTH `/` and `/api/*`; (3) the SPA catch-all serves index.html for non-API routes
WITHOUT shadowing the API.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import main as engine_api  # engine_api/main.py  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(engine_api.app)


def test_health_no_auth():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_whoami_reflects_forwarded_identity_and_steward(monkeypatch):
    monkeypatch.setenv("APP_STEWARD", "steward@x")
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "steward": "steward@x"}


def test_whoami_steward_none_when_unset(monkeypatch):
    monkeypatch.delenv("APP_STEWARD", raising=False)
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "steward": None}


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
    monkeypatch.setenv("APP_ALLOW_BEARER_OBO", "1")  # legacy app-to-app path
    captured = {}

    def fake_list_spaces(profile=None, *, client=None, user_token=None):
        captured["user_token"] = user_token
        return []

    monkeypatch.setattr(engine_api.app_logic, "list_spaces", fake_list_spaces)
    r = client.get("/spaces", headers={"authorization": "Bearer tok-bearer"})
    assert r.status_code == 200
    assert captured["user_token"] == "tok-bearer"


def test_spaces_ignores_bearer_when_flag_off(monkeypatch):
    # The single front-door app does NOT enable Bearer — a client-supplied Authorization is
    # ignored (no x-forwarded-access-token -> 401), so it can't be used to spoof OBO.
    monkeypatch.delenv("APP_ALLOW_BEARER_OBO", raising=False)
    r = client.get("/spaces", headers={"authorization": "Bearer attacker-token"})
    assert r.status_code == 401


def test_spaces_proxy_header_wins_over_bearer(monkeypatch):
    monkeypatch.setenv("APP_ALLOW_BEARER_OBO", "1")
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


def test_spaces_rejects_authorization_without_bearer_scheme(monkeypatch):
    monkeypatch.setenv("APP_ALLOW_BEARER_OBO", "1")
    r = client.get("/spaces", headers={"authorization": "tok-no-scheme"})
    assert r.status_code == 401


def test_spaces_rejects_empty_bearer(monkeypatch):
    monkeypatch.setenv("APP_ALLOW_BEARER_OBO", "1")
    r = client.get("/spaces", headers={"authorization": "Bearer "})
    assert r.status_code == 401


def test_spaces_obo_auth_failure_maps_to_401(monkeypatch):
    # A bad/expired OBO token is the user's problem (re-auth), NOT a 502 engine outage.
    from databricks.sdk.errors import Unauthenticated

    def boom(profile=None, *, client=None, user_token=None):
        raise Unauthenticated("token expired")

    monkeypatch.setattr(engine_api.app_logic, "list_spaces", boom)
    r = client.get("/spaces", headers={"x-forwarded-access-token": "stale"})
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


# --- /api/* mount (SV1): the SPA calls the API under /api; same handlers as the legacy paths ---


def test_api_prefix_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_api_prefix_whoami(monkeypatch):
    monkeypatch.setenv("APP_STEWARD", "steward@x")
    r = client.get("/api/whoami", headers={"x-forwarded-email": "m@x"})
    assert r.status_code == 200 and r.json() == {"email": "m@x", "steward": "steward@x"}


def test_api_prefix_spaces_threads_token(monkeypatch):
    captured = {}
    monkeypatch.setattr(engine_api.app_logic, "list_spaces",
                        lambda profile=None, *, client=None, user_token=None:
                        captured.update(user_token=user_token) or [{"space_id": "a", "title": "T"}])
    r = client.get("/api/spaces", headers={"x-forwarded-access-token": "tok-api"})
    assert r.status_code == 200
    assert r.json() == {"spaces": [{"space_id": "a", "title": "T"}]}
    assert captured["user_token"] == "tok-api"


def test_api_prefix_spaces_requires_token():
    assert client.get("/api/spaces").status_code == 401


# --- SPA static serving (SV1): index.html fallback for non-API routes, real files served ---


@pytest.fixture()
def static_build(tmp_path, monkeypatch):
    """A fake built SPA dir (index.html + one asset), wired via APP_STATIC_DIR."""
    (tmp_path / "index.html").write_text("<!doctype html><div id=app>SPA-ROOT</div>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi')")
    monkeypatch.setenv("APP_STATIC_DIR", str(tmp_path))
    return tmp_path


def test_spa_serves_index_at_root(static_build):
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA-ROOT" in r.text
    assert "text/html" in r.headers["content-type"]


def test_spa_serves_index_for_deep_link(static_build):
    r = client.get("/revisao/qualquer-rota")
    assert r.status_code == 200 and "SPA-ROOT" in r.text


def test_spa_serves_real_asset_file(static_build):
    r = client.get("/assets/app.js")
    assert r.status_code == 200 and "console.log" in r.text


def test_spa_does_not_shadow_api(static_build):
    # Even with the SPA mounted, the API still answers (not the index shell).
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/spaces").status_code == 401  # API 401, not an HTML shell


def test_spa_unknown_api_path_is_json_404(static_build):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {"detail": "not found"}
    assert "SPA-ROOT" not in r.text


def test_spa_placeholder_when_no_build(monkeypatch):
    # No static dir resolvable -> a 200 placeholder, never a 500.
    monkeypatch.setenv("APP_STATIC_DIR", "/nonexistent/path/xyz")
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA build not found" in r.text


def test_promote_threads_token_email_and_returns_review_and_pr(monkeypatch):
    captured = {}

    def fake_request_promotion(space_id, *a, user_token=None, requester_email=None, **k):
        captured.update(space_id=space_id, user_token=user_token, requester_email=requester_email)
        return {"review": {"gate": {"conclusion": "success"}}, "pr": {"number": 7, "url": "u"}}

    monkeypatch.setattr(engine_api.app_logic, "request_promotion", fake_request_promotion)
    r = client.post("/api/promote", json={"space_id": "s1"},
                    headers={"x-forwarded-access-token": "tok", "x-forwarded-email": "m@x"})
    assert r.status_code == 200
    assert r.json()["pr"] == {"number": 7, "url": "u"}
    assert captured == {"space_id": "s1", "user_token": "tok", "requester_email": "m@x"}


def test_promote_requires_a_token():
    assert client.post("/api/promote", json={"space_id": "s"}).status_code == 401


def test_promote_github_error_maps_to_503(monkeypatch):
    from github_app import GitHubError

    def boom(space_id, *a, **k):
        raise GitHubError(404, "create pull", {"message": "not installed"})

    monkeypatch.setattr(engine_api.app_logic, "request_promotion", boom)
    r = client.post("/api/promote", json={"space_id": "s"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 503
    assert "not installed" not in r.text  # internal detail not leaked


def test_spa_rejects_path_traversal(static_build):
    # The security-load-bearing guard: a `..` escape must NOT serve a file outside the static dir.
    # Call the route fn directly — httpx/Starlette would normalize `..` out of a URL before it
    # reaches the route, so this asserts the guard itself rather than the client's normalization.
    secret = static_build.parent / "secret.txt"
    secret.write_text("TOP-SECRET")
    resp = engine_api.spa("../secret.txt")
    # Falls through the traversal guard to the SPA shell (index.html), never the secret file.
    body = resp.path if hasattr(resp, "path") else ""
    assert "secret.txt" not in str(body)
    assert str(body).endswith("index.html")

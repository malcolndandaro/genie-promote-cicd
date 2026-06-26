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
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "steward": "steward@x", "is_admin": False}


def test_whoami_steward_none_when_unset(monkeypatch):
    monkeypatch.delenv("APP_STEWARD", raising=False)
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "steward": None, "is_admin": False}


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
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    r = client.get("/api/whoami", headers={"x-forwarded-email": "m@x"})
    assert r.status_code == 200 and r.json() == {"email": "m@x", "steward": "steward@x", "is_admin": False}


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


def test_promote_status_returns_bot_read(monkeypatch):
    monkeypatch.setattr(engine_api.app_logic, "promotion_status",
                        lambda number, *a, **k: {"phase": "deployed", "merged": True, "number": number})
    r = client.get("/api/promote/6/status")
    assert r.status_code == 200
    assert r.json() == {"phase": "deployed", "merged": True, "number": 6}


def test_promote_status_github_error_maps_to_503(monkeypatch):
    from github_app import GitHubError

    def boom(number, *a, **k):
        raise GitHubError(503, "actions runs", None)

    monkeypatch.setattr(engine_api.app_logic, "promotion_status", boom)
    assert client.get("/api/promote/6/status").status_code == 503


# --- LB3: persistence on request + recover-on-open (no reviewer re-run) -----

from promotion_store import InMemoryBackend, PromotionStore  # noqa: E402


@pytest.fixture()
def store():
    """A real PromotionStore over the in-memory backend, wired into app.state for the request — so
    the persistence + read endpoints are exercised end-to-end with no live Lakebase."""
    s = PromotionStore(InMemoryBackend())
    engine_api.app.state.store = s
    yield s
    engine_api.app.state.store = None


def _fake_promote(review_gate="success", number=7):
    def fn(space_id, *a, user_token=None, requester_email=None, **k):
        return {"review": {"gate": {"conclusion": review_gate, "summary": "ok"},
                           "findings": [{"rule_id": "ENV-01", "severity": "BLOCKER"}] if review_gate == "failure" else [],
                           "eval": {"status": "pass", "summary": "n/a"},
                           "timeline": [{"key": "review", "status": "pass"}]},
                "pr": {"number": number, "url": f"https://gh/pr/{number}"}}
    return fn


def test_promote_persists_promotion_snapshot_and_requested_event(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote())
    r = client.post("/api/promote", json={"space_id": "s1", "resource_title": "Recebíveis"},
                    headers={"x-forwarded-access-token": "tok", "x-forwarded-email": "ana@x"})
    assert r.status_code == 200
    pid = r.json()["promotion_id"]
    p = store.get_promotion(pid)
    assert p.pr_number == 7 and p.resource_title == "Recebíveis" and p.requester_email == "ana@x"
    assert store.latest_snapshot(pid).gate_conclusion == "success"
    events = store.list_audit_events(pid)
    assert [e.event_type for e in events] == ["requested"]
    assert events[0].actor_app_email == "ana@x"  # display-only attribution


def test_promote_rerequest_adds_snapshot_and_re_reviewed_to_same_promotion(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=7))
    h = {"x-forwarded-access-token": "tok", "x-forwarded-email": "ana@x"}
    pid1 = client.post("/api/promote", json={"space_id": "s1"}, headers=h).json()["promotion_id"]
    pid2 = client.post("/api/promote", json={"space_id": "s1"}, headers=h).json()["promotion_id"]
    assert pid1 == pid2  # SAME Promotion (1:1 with the PR) — no fragmentation
    assert len(store.list_snapshots(pid1)) == 2  # both snapshots retained
    assert [e.event_type for e in store.list_audit_events(pid1)] == ["requested", "re_reviewed"]


def test_list_promotions_scope_mine_filters_by_email(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=11))
    client.post("/api/promote", json={"space_id": "s1"},
                headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=12))
    client.post("/api/promote", json={"space_id": "s2"},
                headers={"x-forwarded-access-token": "t", "x-forwarded-email": "bob@x"})
    r = client.get("/api/promotions?scope=mine", headers={"x-forwarded-email": "ana@x"})
    assert r.status_code == 200
    prs = [p["pr_number"] for p in r.json()["promotions"]]
    assert prs == [11]  # only ana's


def test_get_promotion_recovers_stored_review_without_calling_engine(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(review_gate="failure", number=9))
    pid = client.post("/api/promote", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"}).json()["promotion_id"]

    # Recovery must NOT re-run the reviewer: blow up request_promotion so any call would fail.
    def explode(*a, **k):
        raise AssertionError("recovery must NOT call request_promotion (no reviewer re-run)")

    monkeypatch.setattr(engine_api.app_logic, "request_promotion", explode)
    r = client.get(f"/api/promotions/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["pr"] == {"number": 9, "url": "https://gh/pr/9"}
    assert body["review"]["gate"]["conclusion"] == "failure"
    assert body["review"]["gate"]["blocker_count"] == 1  # derived from the stored findings
    assert body["review"]["timeline"] == [{"key": "review", "status": "pass"}]


def test_promote_recovers_from_concurrent_create_race(monkeypatch, store):
    # TOCTOU: a concurrent request created the Promotion for this PR between our find_by_pr and
    # create. create raises DuplicatePRNumber; we must recover (treat as re_reviewed), not 500.
    p = store.create_promotion(resource_id="s1", resource_kind="genie_space", resource_title="t",
                               requester_email="ana@x", pr_number=7, pr_url="u", branch="b",
                               current_phase="open", live_status=None)
    real_find = store.find_by_pr
    seq = {"n": 0}

    def flaky_find(pr_number):  # None on the first lookup (the race window), real value after
        seq["n"] += 1
        return None if seq["n"] == 1 else real_find(pr_number)

    monkeypatch.setattr(store, "find_by_pr", flaky_find)
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=7))
    r = client.post("/api/promote", json={"space_id": "s1"},
                    headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"})
    assert r.status_code == 200
    assert r.json()["promotion_id"] == p.id  # recovered onto the existing promotion, no 500
    assert [e.event_type for e in store.list_audit_events(p.id)] == ["re_reviewed"]


def test_re_review_only_requested_carries_app_email_and_bumps_updated_at(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=7))
    h = {"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"}
    pid = client.post("/api/promote", json={"space_id": "s1"}, headers=h).json()["promotion_id"]
    created_at = store.get_promotion(pid).created_at
    client.post("/api/promote", json={"space_id": "s1"}, headers=h)  # re-review
    events = store.list_audit_events(pid)
    assert events[0].event_type == "requested" and events[0].actor_app_email == "ana@x"
    # re_reviewed carries NO app email (ADR-0005: actor_app_email only on `requested`)
    assert events[1].event_type == "re_reviewed" and events[1].actor_app_email is None
    assert store.get_promotion(pid).updated_at > created_at  # re-review bumped freshness


def test_whoami_reports_admin_from_config(monkeypatch):
    monkeypatch.setenv("APP_ADMINS", "admin@x, boss@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.setenv("APP_STEWARD", "pedro@x")
    assert client.get("/whoami", headers={"x-forwarded-email": "ADMIN@x"}).json()["is_admin"] is True
    assert client.get("/whoami", headers={"x-forwarded-email": "pedro@x"}).json()["is_admin"] is True  # steward
    assert client.get("/whoami", headers={"x-forwarded-email": "rando@x"}).json()["is_admin"] is False


def test_scope_all_role_gated(monkeypatch, store):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=21))
    client.post("/api/promote", json={"space_id": "s1"},
                headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=22))
    client.post("/api/promote", json={"space_id": "s2"},
                headers={"x-forwarded-access-token": "t", "x-forwarded-email": "bob@x"})
    # An admin sees ALL promotions (cross-user governance view).
    admin = client.get("/api/promotions?scope=all", headers={"x-forwarded-email": "admin@x"})
    assert admin.status_code == 200 and {p["pr_number"] for p in admin.json()["promotions"]} == {21, 22}
    # A non-admin requesting scope=all is denied (server-enforced, not just hidden in the UI).
    assert client.get("/api/promotions?scope=all", headers={"x-forwarded-email": "ana@x"}).status_code == 403
    # scope=mine still filters by the caller.
    mine = client.get("/api/promotions?scope=mine", headers={"x-forwarded-email": "ana@x"})
    assert {p["pr_number"] for p in mine.json()["promotions"]} == {21}
    assert client.get("/api/promotions?scope=bogus", headers={"x-forwarded-email": "admin@x"}).status_code == 400


def test_status_read_reconciles_and_caches(monkeypatch, store):
    # Persist a promotion (PR 6), then a status read must reconcile -> append GitHub events + cache.
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=6))
    pid = client.post("/api/promote", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"}).json()["promotion_id"]
    monkeypatch.setattr(engine_api.app_logic, "promotion_status", lambda n, *a, **k: {
        "pr_state": "open", "merged": False, "checks": "pending", "review_decision": "approved",
        "deploy": {"status": "none", "conclusion": None, "waiting_approval": False, "approver": None},
        "pr_url": "https://gh/pr/6", "phase": "open"})
    # github_app_factory only fires if a merge/approval identity is needed; provide a fake.
    monkeypatch.setattr(engine_api.app_logic, "github_app_factory",
                        lambda *a, **k: type("G", (), {"audit_facts": lambda self, n: {"review_approver": "PSPedro176"}})())
    r = client.get("/api/promote/6/status")
    assert r.status_code == 200 and r.json()["phase"] == "open"
    types = [e.event_type for e in store.list_audit_events(pid)]
    assert "pr_opened" in types and "pr_review_approved" in types  # reconciled from GitHub
    assert store.get_promotion(pid).current_phase == "open"  # cache updated


def test_promotion_detail_includes_audit_and_cached_status(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=6))
    pid = client.post("/api/promote", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"}).json()["promotion_id"]
    body = client.get(f"/api/promotions/{pid}").json()
    assert "audit" in body and body["audit"][0]["event_type"] == "requested"
    assert body["audit"][0]["actor_app_email"] == "ana@x"  # display-only attribution
    assert "live_status" in body  # cached status for instant render (None until first reconcile)
    audit = client.get(f"/api/promotions/{pid}/audit").json()["audit"]
    assert [e["event_type"] for e in audit] == ["requested"]


def test_get_promotion_ownership_gated(monkeypatch, store):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=31))
    pid = client.post("/api/promote", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "t", "x-forwarded-email": "ana@x"}).json()["promotion_id"]
    # The owner sees it; an admin sees it; another user is denied (can't read by guessing an id).
    assert client.get(f"/api/promotions/{pid}", headers={"x-forwarded-email": "ana@x"}).status_code == 200
    assert client.get(f"/api/promotions/{pid}", headers={"x-forwarded-email": "admin@x"}).status_code == 200
    assert client.get(f"/api/promotions/{pid}", headers={"x-forwarded-email": "mallory@x"}).status_code == 403
    assert client.get(f"/api/promotions/{pid}/audit", headers={"x-forwarded-email": "mallory@x"}).status_code == 403


def test_get_promotion_404_for_unknown(store):
    # No email == local/dev (the deployed proxy always injects it) -> ownership check is skipped.
    assert client.get("/api/promotions/nope").status_code == 404


def test_promotions_endpoints_503_without_store():
    engine_api.app.state.store = None  # explicit: no Lakebase bound
    assert client.get("/api/promotions").status_code == 503
    assert client.get("/api/promotions/x").status_code == 503


def test_promote_still_works_without_store(monkeypatch):
    # Local-without-Lakebase: the PR still opens; persistence is skipped (no promotion_id).
    engine_api.app.state.store = None
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=42))
    r = client.post("/api/promote", json={"space_id": "s"},
                    headers={"x-forwarded-access-token": "t"})
    assert r.status_code == 200 and "promotion_id" not in r.json()


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

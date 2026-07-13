"""Unit tests for the single-app FastAPI engine (AK1 + SV1). FastAPI TestClient + a mocked engine
— no network.

Verifies (1) the transport + OBO plumbing: the user token is resolved from either header source
and threaded to the engine as `user_token`; the missing-token path is a 401; (2) the API is
served at BOTH `/` and `/api/*`; (3) the SPA catch-all serves index.html for non-API routes
WITHOUT shadowing the API; (4) A2 — authorization (`is_admin`, promotion visibility) is derived
from the platform-VERIFIED identity behind the OBO token, NEVER from the display-only
`x-forwarded-email` header (see `_verify_as` below, which fakes `authz.verify_identity` so tests
never hit a live workspace).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import authz  # noqa: E402  (app/authz.py — on sys.path via the app/ insert above)
import main as engine_api  # engine_api/main.py  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    """Fake `authz.verify_identity` so a given OBO token string "verifies" to a given email/
    user_name, with NO live workspace call. Any token not in the mapping raises AccessDenied
    (fail-closed), matching a token that doesn't resolve on a real workspace."""
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


def test_health_no_auth():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_whoami_reflects_forwarded_identity_and_steward(monkeypatch):
    monkeypatch.setenv("APP_STEWARD", "steward@x")
    monkeypatch.setenv("APP_REPO_URL", "https://gh/acme/repo")
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_DEV_HOST", raising=False)
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "steward": "steward@x", "is_admin": False,
                        "repo_url": "https://gh/acme/repo", "dev_host": None}


def test_whoami_steward_none_when_unset(monkeypatch):
    monkeypatch.delenv("APP_STEWARD", raising=False)
    monkeypatch.setenv("APP_REPO_URL", "https://gh/acme/repo")
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_DEV_HOST", raising=False)
    r = client.get("/whoami", headers={"x-forwarded-email": "malcoln@x"})
    assert r.status_code == 200
    assert r.json() == {"email": "malcoln@x", "steward": None, "is_admin": False,
                        "repo_url": "https://gh/acme/repo", "dev_host": None}


def test_whoami_repo_url_defaults_when_unset(monkeypatch):
    # Config-driven with a safe default (ADR-0004): no APP_REPO_URL -> the accelerator's own repo.
    monkeypatch.delenv("APP_REPO_URL", raising=False)
    body = client.get("/whoami", headers={"x-forwarded-email": "m@x"}).json()
    assert body["repo_url"] == "https://github.com/malcolndandaro/genie-promote-cicd"


def test_whoami_dev_host_reflects_env(monkeypatch):
    # G5: the standalone rehydrate screen builds a deep-link to the resulting dev Space from this.
    monkeypatch.setenv("APP_DEV_HOST", "https://dev.cloud.databricks.com")
    body = client.get("/whoami", headers={"x-forwarded-email": "m@x"}).json()
    assert body["dev_host"] == "https://dev.cloud.databricks.com"


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


# --- GET /prod-spaces + /principals (G1: prefilled/searchable pickers) ---


def test_prod_spaces_requires_a_token():
    r = client.get("/prod-spaces")
    assert r.status_code == 401


def test_prod_spaces_returns_id_and_title(monkeypatch):
    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces",
                        lambda: [{"space_id": "a", "title": "Recebíveis"},
                                 {"space_id": "b", "title": "Outro Espaço"}])
    r = client.get("/prod-spaces", headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"spaces": [{"space_id": "a", "title": "Recebíveis"},
                                   {"space_id": "b", "title": "Outro Espaço"}]}


def test_prod_spaces_filters_by_title_case_insensitively(monkeypatch):
    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces",
                        lambda: [{"space_id": "a", "title": "Recebíveis"},
                                 {"space_id": "b", "title": "Outro Espaço"}])
    r = client.get("/prod-spaces", params={"q": "receb"}, headers={"x-forwarded-access-token": "tok"})
    assert r.json() == {"spaces": [{"space_id": "a", "title": "Recebíveis"}]}


def test_principals_requires_a_token():
    r = client.get("/principals")
    assert r.status_code == 401


def test_principals_threads_query_and_kind(monkeypatch):
    captured = {}

    def fake_list_principals(q="", *, profile=None, client=None, user_token=None, kind="all", limit=25):
        captured.update(q=q, user_token=user_token, kind=kind)
        return [{"type": "user", "id": "u1", "display": "Ana", "email": "ana@x.com"}]

    monkeypatch.setattr(engine_api.app_logic, "list_principals", fake_list_principals)
    r = client.get("/principals", params={"q": "ana", "kind": "user"},
                   headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"principals": [{"type": "user", "id": "u1", "display": "Ana", "email": "ana@x.com"}]}
    assert captured == {"q": "ana", "user_token": "tok", "kind": "user"}


def test_principals_rejects_invalid_kind():
    r = client.get("/principals", params={"kind": "bogus"},
                   headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 400


# --- POST /review (AK3) ---

_FAKE_REVIEW = {
    "findings": [{"rule_id": "EVAL-01", "severity": "BLOCKER", "message": "...", "citation": "..."}],
    "gate": {"conclusion": "failure", "blocker_count": 1, "summary": "🔴 ..."},
    "eval": {"status": "advisory", "summary": "🟡 ..."},
    "timeline": [{"key": "checks", "label": "...", "status": "pass"}],
    "allowlist_violations": [],
    "consumer_group": "account users",
    "access_spec": {"space_permissions": [], "uc_principals": []},  # F2: no access declared
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
                                "allowlist_violations", "consumer_group", "access_spec"}
    assert "prod_serialized" not in body  # large payload dropped
    assert body["gate"]["conclusion"] == "failure"


def test_review_requires_a_token():
    r = client.post("/review", json={"space_id": "x"})
    assert r.status_code == 401


# --- F2: AccessSpec threaded through /review and /promote --------------------

_ACCESS_SPEC_WIRE = {
    "space_permissions": [{"principal": "data_analysts", "is_group": True, "level": "CAN_RUN"}],
    "uc_principals": [{"principal": "ana@x.com", "is_group": False}],
}


def test_review_threads_declared_access_spec_into_the_engine(monkeypatch):
    captured = {}

    def fake_review_space(space_id, *a, access_spec_=None, **k):
        captured["access_spec_"] = access_spec_
        out = dict(_FAKE_REVIEW)
        out["access_spec"] = access_spec_.to_dict() if access_spec_ else out["access_spec"]
        return out

    monkeypatch.setattr(engine_api.app_logic, "review_space", fake_review_space)
    r = client.post("/review", json={"space_id": "01f16e83", "access_spec": _ACCESS_SPEC_WIRE},
                    headers={"x-forwarded-access-token": "tok-r"})
    assert r.status_code == 200
    assert captured["access_spec_"] is not None
    assert captured["access_spec_"].to_dict() == _ACCESS_SPEC_WIRE
    assert r.json()["access_spec"] == _ACCESS_SPEC_WIRE


def test_review_without_access_spec_passes_none_through(monkeypatch):
    """The pre-F2 contract ({space_id} only) must still work — no AccessSpec declared -> None,
    never an empty-but-truthy sentinel that would confuse a downstream check."""
    captured = {}

    def fake_review_space(space_id, *a, access_spec_=None, **k):
        captured["access_spec_"] = access_spec_
        return dict(_FAKE_REVIEW)

    monkeypatch.setattr(engine_api.app_logic, "review_space", fake_review_space)
    r = client.post("/review", json={"space_id": "01f16e83"},
                    headers={"x-forwarded-access-token": "tok-r"})
    assert r.status_code == 200
    assert captured["access_spec_"] is None


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


# --- /rehydrate (A3/F1): prod->dev reseed, no git PR --------------------------------------------


def test_rehydrate_requires_a_token():
    r = client.post("/rehydrate", json={"source_prod_space_id": "x", "mode": "create"})
    assert r.status_code == 401


def test_rehydrate_rejects_invalid_mode(monkeypatch):
    _verify_as(monkeypatch, {"tok": "ana@x"})
    r = client.post("/rehydrate", json={"source_prod_space_id": "x", "mode": "delete"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 400


def test_rehydrate_overwrite_requires_dev_space_id(monkeypatch):
    _verify_as(monkeypatch, {"tok": "ana@x"})
    r = client.post("/rehydrate", json={"source_prod_space_id": "x", "mode": "overwrite"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 400


def test_rehydrate_create_returns_new_space_id(monkeypatch):
    _verify_as(monkeypatch, {"tok": "ana@x"})

    def fake_rehydrate(*, source_prod_space_id, identity, mode, dev_space_id=None, title=None,
                       store=None, promotion_id=None):
        assert identity.user_name == "ana@x"  # the VERIFIED identity, not a header
        assert mode == "create"
        return engine_api.rehydrate_mod.RehydrateResult(
            space_id="new-dev-id", mode="create", title=title, warehouse_id="wh-1")

    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space", fake_rehydrate)
    r = client.post("/rehydrate",
                    json={"source_prod_space_id": "prod-1", "mode": "create", "title": "Recebíveis (dev)"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 200
    assert r.json() == {"space_id": "new-dev-id", "mode": "create", "title": "Recebíveis (dev)"}


def test_rehydrate_overwrite_passes_dev_space_id_and_promotion_id(monkeypatch):
    _verify_as(monkeypatch, {"tok": "ana@x"})
    seen = {}

    def fake_rehydrate(*, source_prod_space_id, identity, mode, dev_space_id=None, title=None,
                       store=None, promotion_id=None):
        seen.update(dev_space_id=dev_space_id, promotion_id=promotion_id, store=store)
        return engine_api.rehydrate_mod.RehydrateResult(
            space_id=dev_space_id, mode=mode, title=title, warehouse_id="wh-1")

    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space", fake_rehydrate)
    r = client.post("/rehydrate", json={
        "source_prod_space_id": "prod-1", "mode": "overwrite",
        "dev_space_id": "dev-1", "promotion_id": "promo-1",
    }, headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 200
    assert r.json()["space_id"] == "dev-1"
    assert seen["dev_space_id"] == "dev-1" and seen["promotion_id"] == "promo-1"


def _mk_promotion(store):
    return store.create_promotion(
        resource_id="dev-src", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@x", pr_number=7, pr_url="https://gh/pr/7", branch="promote/x",
        current_phase="deployed", live_status=None)


def test_rehydrate_requires_promotion_id_when_store_present(monkeypatch, store):
    # A3 review Finding 1: with a durable store (prod), a rehydrate mutates a real dev Space and MUST
    # be auditable -> reject (before any mutation) when there's no source Promotion to attach the
    # audit event to. Without this, POST /rehydrate {no promotion_id} created/overwrote a dev Space
    # with ZERO audit rows (non-repudiation gap).
    _verify_as(monkeypatch, {"tok": "ana@x"})
    called = {"ran": False}
    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space",
                        lambda *a, **k: called.__setitem__("ran", True))
    r = client.post("/rehydrate", json={"source_prod_space_id": "prod-1", "mode": "create"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 400
    assert called["ran"] is False  # rejected before the service (no mutation)


def test_rehydrate_rejects_unknown_promotion_id_when_store_present(monkeypatch, store):
    _verify_as(monkeypatch, {"tok": "ana@x"})
    called = {"ran": False}
    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space",
                        lambda *a, **k: called.__setitem__("ran", True))
    r = client.post("/rehydrate",
                    json={"source_prod_space_id": "prod-1", "mode": "create", "promotion_id": "nope"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 404
    assert called["ran"] is False


def test_rehydrate_with_valid_promotion_id_runs_when_store_present(monkeypatch, store):
    _verify_as(monkeypatch, {"tok": "ana@x"})
    p = _mk_promotion(store)

    def fake_rehydrate(*, source_prod_space_id, identity, mode, dev_space_id=None, title=None,
                       store=None, promotion_id=None):
        assert store is not None and promotion_id == p.id  # linkage threaded through -> auditable
        return engine_api.rehydrate_mod.RehydrateResult(
            space_id="new-dev-id", mode="create", title=title, warehouse_id="wh-1")

    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space", fake_rehydrate)
    r = client.post("/rehydrate",
                    json={"source_prod_space_id": "prod-1", "mode": "create", "promotion_id": p.id},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 200
    assert r.json()["space_id"] == "new-dev-id"


def test_rehydrate_auto_resolves_promotion_id_by_resource_id_when_omitted(monkeypatch, store):
    # G5: the standalone "Exportar para Dev" screen picks a prod Space from getProdSpaces (title+id
    # only) and never sends promotion_id — the endpoint must resolve it from source_prod_space_id
    # itself so that flow stays auditable without the client knowing what a promotion_id is.
    _verify_as(monkeypatch, {"tok": "ana@x"})
    p = store.create_promotion(
        resource_id="prod-1", resource_kind="genie_space", resource_title="Recebíveis",
        requester_email="ana@x", pr_number=9, pr_url="https://gh/pr/9", branch="promote/x",
        current_phase="deployed", live_status=None)

    def fake_rehydrate(*, source_prod_space_id, identity, mode, dev_space_id=None, title=None,
                       store=None, promotion_id=None):
        assert store is not None and promotion_id == p.id  # resolved, not just accepted as None
        return engine_api.rehydrate_mod.RehydrateResult(
            space_id="new-dev-id", mode="create", title=title, warehouse_id="wh-1")

    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space", fake_rehydrate)
    r = client.post("/rehydrate", json={"source_prod_space_id": "prod-1", "mode": "create"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 200
    assert r.json()["space_id"] == "new-dev-id"


def test_rehydrate_resolution_does_not_match_a_different_resource(monkeypatch, store):
    # A Promotion exists, but for a DIFFERENT resource — must not be picked up as a false match; the
    # request still 400s exactly like the empty-store case (rehydrate stays auditable, never guesses).
    _verify_as(monkeypatch, {"tok": "ana@x"})
    _mk_promotion(store)  # resource_id="dev-src", unrelated to "prod-1" below
    called = {"ran": False}
    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space",
                        lambda *a, **k: called.__setitem__("ran", True))
    r = client.post("/rehydrate", json={"source_prod_space_id": "prod-1", "mode": "create"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 400
    assert called["ran"] is False


def test_rehydrate_access_denied_maps_to_403_not_bootstrap_error(monkeypatch):
    _verify_as(monkeypatch, {"tok": "mallory@x"})

    def boom(**kw):
        raise authz.AccessDenied("mallory@x may not access space prod-1")

    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space", boom)
    r = client.post("/rehydrate", json={"source_prod_space_id": "prod-1", "mode": "create"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 403


def test_rehydrate_dev_not_bootstrapped_maps_to_503_distinct_from_403(monkeypatch):
    _verify_as(monkeypatch, {"tok": "ana@x"})

    def boom(**kw):
        raise engine_api.rehydrate_mod.DevEnvironmentNotBootstrapped(
            "dev environment needs re-bootstrap (run provision_dev_sp.sh): APP_DEV_HOST not set")

    monkeypatch.setattr(engine_api.rehydrate_mod, "rehydrate_space", boom)
    r = client.post("/rehydrate", json={"source_prod_space_id": "prod-1", "mode": "create"},
                    headers={"x-forwarded-access-token": "tok"})
    assert r.status_code == 503
    assert "provision_dev_sp.sh" in r.json()["detail"]


# --- /api/* mount (SV1): the SPA calls the API under /api; same handlers as the legacy paths ---


def test_api_prefix_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_api_prefix_whoami(monkeypatch):
    monkeypatch.setenv("APP_STEWARD", "steward@x")
    monkeypatch.setenv("APP_REPO_URL", "https://gh/acme/repo")
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_DEV_HOST", raising=False)
    r = client.get("/api/whoami", headers={"x-forwarded-email": "m@x"})
    assert r.status_code == 200 and r.json() == {"email": "m@x", "steward": "steward@x",
                                                 "is_admin": False, "repo_url": "https://gh/acme/repo",
                                                 "dev_host": None}


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


def test_promote_threads_access_spec_to_the_engine_and_persists_it(monkeypatch, store):
    """F2: the declared AccessSpec is (1) threaded into app_logic.request_promotion — the app-direct
    DECLARATION step (no live grant/permission call happens here, that's the CI-run governed path)
    — and (2) persisted WITH the Promotion, independent of the review snapshot."""
    captured = {}

    def fake_request_promotion(space_id, *a, access_spec_=None, **k):
        captured["access_spec_"] = access_spec_
        review = {"gate": {"conclusion": "success", "summary": "ok"}, "findings": [],
                 "eval": {"status": "pass", "summary": "n/a"}, "timeline": [],
                 "access_spec": access_spec_.to_dict() if access_spec_ else {"space_permissions": [], "uc_principals": []}}
        return {"review": review, "pr": {"number": 42, "url": "https://gh/pr/42"}}

    monkeypatch.setattr(engine_api.app_logic, "request_promotion", fake_request_promotion)
    r = client.post("/api/promote", json={"space_id": "s1", "access_spec": _ACCESS_SPEC_WIRE},
                    headers={"x-forwarded-access-token": "tok", "x-forwarded-email": "ana@x"})
    assert r.status_code == 200
    assert captured["access_spec_"].to_dict() == _ACCESS_SPEC_WIRE

    pid = r.json()["promotion_id"]
    p = store.get_promotion(pid)
    assert p.access_spec == _ACCESS_SPEC_WIRE

    # the Steward-facing recovery payload (get_promotion) echoes the SAME declaration
    detail = client.get(f"/api/promotions/{pid}",
                        headers={"x-forwarded-access-token": "tok"}).json()
    assert detail["promotion"]["access_spec"] == _ACCESS_SPEC_WIRE


def test_promote_without_access_spec_persists_none(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote())
    r = client.post("/api/promote", json={"space_id": "s1"},
                    headers={"x-forwarded-access-token": "tok", "x-forwarded-email": "ana@x"})
    pid = r.json()["promotion_id"]
    assert store.get_promotion(pid).access_spec is None


def test_promote_rerequest_adds_snapshot_and_re_reviewed_to_same_promotion(monkeypatch, store):
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=7))
    h = {"x-forwarded-access-token": "tok", "x-forwarded-email": "ana@x"}
    pid1 = client.post("/api/promote", json={"space_id": "s1"}, headers=h).json()["promotion_id"]
    pid2 = client.post("/api/promote", json={"space_id": "s1"}, headers=h).json()["promotion_id"]
    assert pid1 == pid2  # SAME Promotion (1:1 with the PR) — no fragmentation
    assert len(store.list_snapshots(pid1)) == 2  # both snapshots retained
    assert [e.event_type for e in store.list_audit_events(pid1)] == ["requested", "re_reviewed"]


def test_list_promotions_scope_mine_filters_by_email(monkeypatch, store):
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-bob": "bob@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=11))
    client.post("/api/promote", json={"space_id": "s1"},
                headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=12))
    client.post("/api/promote", json={"space_id": "s2"},
                headers={"x-forwarded-access-token": "tok-bob", "x-forwarded-email": "bob@x"})
    # scope=mine is keyed on the VERIFIED identity (A2) — the request must carry a token that
    # verifies to ana's email, not just a matching display header.
    r = client.get("/api/promotions?scope=mine", headers={"x-forwarded-access-token": "tok-ana",
                                                           "x-forwarded-email": "ana@x"})
    assert r.status_code == 200
    prs = [p["pr_number"] for p in r.json()["promotions"]]
    assert prs == [11]  # only ana's


def test_list_promotions_scope_mine_spoofed_header_does_not_leak_others(monkeypatch, store):
    # A caller whose OWN verified identity is bob cannot see ana's promotions by merely sending
    # ana's email as the display header (A2: x-forwarded-email is never an authz input).
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-bob": "bob@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=11))
    client.post("/api/promote", json={"space_id": "s1"},
                headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"})
    r = client.get("/api/promotions?scope=mine",
                   headers={"x-forwarded-access-token": "tok-bob", "x-forwarded-email": "ana@x"})
    assert r.status_code == 200
    assert r.json()["promotions"] == []  # bob's verified identity owns nothing, despite the header


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
    # is_admin is computed from the VERIFIED identity behind the OBO token, never the display
    # header alone — so a real client must present a token that verifies to the admin email.
    monkeypatch.setenv("APP_ADMINS", "admin@x, boss@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.setenv("APP_STEWARD", "pedro@x")
    _verify_as(monkeypatch, {"tok-admin": "ADMIN@x", "tok-steward": "pedro@x", "tok-rando": "rando@x"})
    assert client.get("/whoami", headers={"x-forwarded-access-token": "tok-admin",
                                          "x-forwarded-email": "ADMIN@x"}).json()["is_admin"] is True
    assert client.get("/whoami", headers={"x-forwarded-access-token": "tok-steward",
                                          "x-forwarded-email": "pedro@x"}).json()["is_admin"] is True  # steward
    assert client.get("/whoami", headers={"x-forwarded-access-token": "tok-rando",
                                          "x-forwarded-email": "rando@x"}).json()["is_admin"] is False


def test_whoami_is_admin_ignores_spoofed_display_header(monkeypatch):
    # A2's whole point: x-forwarded-email is NEVER an authz input. A caller whose OBO token
    # verifies to a non-admin identity cannot become admin by sending a different display header.
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-mallory": "mallory@x"})  # verifies to a NON-admin identity
    r = client.get("/whoami", headers={"x-forwarded-access-token": "tok-mallory",
                                       "x-forwarded-email": "admin@x"})  # spoofed display header
    body = r.json()
    assert body["email"] == "admin@x"  # display-only field still reflects the (untrusted) header
    assert body["is_admin"] is False   # but authorization used the VERIFIED identity, not the header


def test_whoami_unverifiable_token_is_not_admin(monkeypatch):
    # An expired/invalid OBO token must fail closed: no admin, not "trust the header instead".
    monkeypatch.setenv("APP_ADMINS", "admin@x")

    def boom(token, *, host=None):
        raise authz.AccessDenied("token expired")

    monkeypatch.setattr(engine_api.authz, "verify_identity", boom)
    r = client.get("/whoami", headers={"x-forwarded-access-token": "stale-token",
                                       "x-forwarded-email": "admin@x"})
    assert r.status_code == 200
    assert r.json()["is_admin"] is False


def test_scope_all_role_gated(monkeypatch, store):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-bob": "bob@x", "tok-admin": "admin@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=21))
    client.post("/api/promote", json={"space_id": "s1"},
                headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=22))
    client.post("/api/promote", json={"space_id": "s2"},
                headers={"x-forwarded-access-token": "tok-bob", "x-forwarded-email": "bob@x"})
    # An admin (by VERIFIED identity) sees ALL promotions (cross-user governance view).
    admin = client.get("/api/promotions?scope=all", headers={"x-forwarded-access-token": "tok-admin",
                                                             "x-forwarded-email": "admin@x"})
    assert admin.status_code == 200 and {p["pr_number"] for p in admin.json()["promotions"]} == {21, 22}
    # A non-admin requesting scope=all is denied (server-enforced, not just hidden in the UI) —
    # even if they spoof the display header to look like the admin.
    assert client.get("/api/promotions?scope=all",
                      headers={"x-forwarded-access-token": "tok-ana",
                               "x-forwarded-email": "admin@x"}).status_code == 403
    # scope=mine still filters by the caller's VERIFIED identity.
    mine = client.get("/api/promotions?scope=mine", headers={"x-forwarded-access-token": "tok-ana",
                                                              "x-forwarded-email": "ana@x"})
    assert {p["pr_number"] for p in mine.json()["promotions"]} == {21}
    assert client.get("/api/promotions?scope=bogus",
                      headers={"x-forwarded-access-token": "tok-admin",
                               "x-forwarded-email": "admin@x"}).status_code == 400


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
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-admin": "admin@x", "tok-mallory": "mallory@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=31))
    pid = client.post("/api/promote", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}).json()["promotion_id"]
    # The owner sees it; an admin sees it; another user is denied (can't read by guessing an id) —
    # gated on each caller's own VERIFIED identity.
    assert client.get(f"/api/promotions/{pid}",
                      headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}).status_code == 200
    assert client.get(f"/api/promotions/{pid}",
                      headers={"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"}).status_code == 200
    assert client.get(f"/api/promotions/{pid}",
                      headers={"x-forwarded-access-token": "tok-mallory", "x-forwarded-email": "mallory@x"}).status_code == 403
    assert client.get(f"/api/promotions/{pid}/audit",
                      headers={"x-forwarded-access-token": "tok-mallory", "x-forwarded-email": "mallory@x"}).status_code == 403


def test_get_promotion_denies_spoofed_display_header(monkeypatch, store):
    # Mallory cannot see ana's promotion by sending ana's email as the display header — access is
    # gated on mallory's own VERIFIED identity (from mallory's OWN token), not the header (A2).
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-mallory": "mallory@x"})
    monkeypatch.setattr(engine_api.app_logic, "request_promotion", _fake_promote(number=32))
    pid = client.post("/api/promote", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}).json()["promotion_id"]
    r = client.get(f"/api/promotions/{pid}",
                   headers={"x-forwarded-access-token": "tok-mallory", "x-forwarded-email": "ana@x"})
    assert r.status_code == 403


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

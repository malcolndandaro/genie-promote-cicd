"""Unit tests for the G2 endpoints (engine_api/main.py): admin-configurable reviewer rules
(Lakebase-backed, safe hardcoded fallback). FastAPI TestClient + fakes — no network, no live
workspace.

Mirrors tests/test_roles_and_drift_api.py's conventions (`_verify_as`, the admin-gating
parametrized checks, the InMemoryBackend store fixture wired into `engine_api.app.state`).

Verifies: (1) the rules CRUD is admin-gated on the VERIFIED identity, (2) a plain override must
name a real hardcoded rule_id (and a custom rule must NOT collide with one), (3) `GET
/admin/rules` returns the merged effective set, (4) every mutation is audited, and (5) `review()`
threads the store's overrides into the engine (the no-store-identical guarantee end-to-end).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import authz  # noqa: E402
import main as engine_api  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from rules_store import InMemoryBackend as RulesInMemoryBackend  # noqa: E402
from rules_store import RulesStore  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


@pytest.fixture()
def rules_store_fixture():
    s = RulesStore(RulesInMemoryBackend())
    engine_api.app.state.rules_store = s
    yield s
    engine_api.app.state.rules_store = None


ADMIN_HEADERS = {"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"}
RANDO_HEADERS = {"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "rando@x"}


def _setup_admin(monkeypatch):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    monkeypatch.delenv("APP_STEWARDS", raising=False)
    monkeypatch.delenv("APP_STEWARD", raising=False)
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x"})


# --- admin gating (A2-hardened, VERIFIED identity, never the display header) --------------------


def test_list_rules_403_a_non_admin(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/rules", headers=RANDO_HEADERS).status_code == 403


def test_list_rules_403_with_no_token(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/rules").status_code == 403


def test_list_rules_ignores_spoofed_display_header(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.get("/api/admin/rules",
                   headers={"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "admin@x"})
    assert r.status_code == 403


def test_upsert_rule_403_a_non_admin(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules", json={"rule_id": "ENV-01", "enabled": False},
                    headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_reset_rule_403_a_non_admin(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules/reset", json={"rule_id": "ENV-01"}, headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_rules_endpoints_require_the_store(monkeypatch):
    _setup_admin(monkeypatch)
    engine_api.app.state.rules_store = None
    assert client.get("/api/admin/rules", headers=ADMIN_HEADERS).status_code == 503


# --- GET /admin/rules: the effective set + raw overrides ------------------------------------------


def test_list_rules_returns_hardcoded_effective_and_overrides(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    body = client.get("/api/admin/rules", headers=ADMIN_HEADERS).json()
    assert len(body["hardcoded"]) == 7
    assert body["effective"] == body["hardcoded"]  # no overrides yet — byte-identical
    assert body["overrides"] == []


def test_list_rules_reflects_a_disabled_override_in_the_effective_set(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    client.post("/api/admin/rules", json={"rule_id": "SQL-01", "enabled": False}, headers=ADMIN_HEADERS)
    body = client.get("/api/admin/rules", headers=ADMIN_HEADERS).json()
    assert "SQL-01" not in {r["rule_id"] for r in body["effective"]}
    assert len(body["overrides"]) == 1 and body["overrides"][0]["rule_id"] == "SQL-01"


# --- POST /admin/rules: upsert (plain override + custom) validation + audit ----------------------


def test_upsert_plain_override_persists_and_is_audited(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules",
                    json={"rule_id": "EVAL-01", "enabled": True, "severity": "SUGGESTION",
                          "params": {"min_benchmarks": 3}},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200
    rule = r.json()["rule"]
    assert rule["severity"] == "SUGGESTION" and rule["params"] == {"min_benchmarks": 3}
    assert rule["updated_by"] == "admin@x"
    events = rules_store_fixture.list_audit_events("EVAL-01")
    assert [e.event_type for e in events] == ["rule_updated"] and events[0].actor_email == "admin@x"


def test_upsert_rejects_a_plain_override_for_an_unknown_rule_id(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules", json={"rule_id": "NOT-A-RULE", "enabled": False},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_upsert_rejects_a_custom_rule_colliding_with_a_hardcoded_id(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules",
                    json={"rule_id": "ENV-01", "is_custom": True, "severity": "STYLE",
                          "content": "c", "citation": "cit"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


def test_upsert_custom_rule_persists_and_appears_in_the_effective_set(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules",
                    json={"rule_id": "CUSTOM-01", "is_custom": True, "severity": "STYLE",
                          "content": "nunca use SELECT *", "citation": "Padrão interno"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = client.get("/api/admin/rules", headers=ADMIN_HEADERS).json()
    custom = next(r for r in body["effective"] if r["rule_id"] == "CUSTOM-01")
    assert custom["content"] == "nunca use SELECT *"


def test_upsert_custom_rule_missing_content_is_400(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules",
                    json={"rule_id": "CUSTOM-01", "is_custom": True, "severity": "STYLE"},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400


# --- POST /admin/rules/reset: idempotent, audited -------------------------------------------------


def test_reset_rule_restores_the_default_and_is_audited(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    client.post("/api/admin/rules", json={"rule_id": "ENV-02", "enabled": False}, headers=ADMIN_HEADERS)
    r = client.post("/api/admin/rules/reset", json={"rule_id": "ENV-02"}, headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = client.get("/api/admin/rules", headers=ADMIN_HEADERS).json()
    assert body["overrides"] == []
    assert "ENV-02" in {r["rule_id"] for r in body["effective"]}  # back to the hardcoded default
    events = rules_store_fixture.list_audit_events("ENV-02")
    assert [e.event_type for e in events] == ["rule_updated", "rule_reset"]


def test_reset_rule_is_idempotent_when_absent(monkeypatch, rules_store_fixture):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/rules/reset", json={"rule_id": "GHOST-01"}, headers=ADMIN_HEADERS)
    assert r.status_code == 200


# --- the engine actually consumes the store's overrides (end-to-end, mocked review) ---------------


def test_review_uses_effective_rules_from_the_store(monkeypatch, rules_store_fixture):
    """`POST /review` reads `_rules_store()` and threads overrides into `app_logic.review_space` —
    this proves the WIRING (main.py -> app_logic), not the merge logic itself (covered by
    test_rules_config.py)."""
    _setup_admin(monkeypatch)
    client.post("/api/admin/rules", json={"rule_id": "SQL-01", "enabled": False}, headers=ADMIN_HEADERS)

    captured = {}

    def fake_review_space(space_id, **kwargs):
        captured["rule_overrides"] = kwargs.get("rule_overrides")
        return {"findings": [], "gate": {"conclusion": "success", "blocker_count": 0, "summary": "ok"},
                "eval": {"status": "pass", "summary": "ok"}, "timeline": [],
                "allowlist_violations": [],
                "audience_spec": {"principals": [{"principal": "users", "is_group": True}]}}

    monkeypatch.setattr(engine_api.app_logic, "review_space", fake_review_space)
    r = client.post(
        "/api/review",
        json={"space_id": "s1", "audience_spec": {"principals": [
            {"principal": "users", "is_group": True}
        ]}},
                    headers={"x-forwarded-access-token": "tok-user"})
    assert r.status_code == 200
    overrides = captured["rule_overrides"]
    assert overrides is not None
    assert any(o["rule_id"] == "SQL-01" and o["enabled"] is False for o in overrides)


def test_review_passes_none_when_no_rules_store_bound(monkeypatch):
    # No rules_store fixture at all — app.state.rules_store stays None. `review()` must pass
    # rule_overrides=None (not crash), which app_logic/rules_config treat as "use the hardcoded set".
    engine_api.app.state.rules_store = None
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-admin": "admin@x"})

    captured = {}

    def fake_review_space(space_id, **kwargs):
        captured["rule_overrides"] = kwargs.get("rule_overrides")
        return {"findings": [], "gate": {"conclusion": "success", "blocker_count": 0, "summary": "ok"},
                "eval": {"status": "pass", "summary": "ok"}, "timeline": [],
                "allowlist_violations": [],
                "audience_spec": {"principals": [{"principal": "users", "is_group": True}]}}

    monkeypatch.setattr(engine_api.app_logic, "review_space", fake_review_space)
    r = client.post(
        "/api/review",
        json={"space_id": "s1", "audience_spec": {"principals": [
            {"principal": "users", "is_group": True}
        ]}},
                    headers={"x-forwarded-access-token": "tok-user"})
    assert r.status_code == 200
    assert captured["rule_overrides"] is None

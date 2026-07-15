"""Unit tests for the S8 admin endpoints (engine_api/main.py): the reviewer prompt template
editor. FastAPI TestClient + fakes — no network, no live workspace.

Mirrors tests/test_ka_endpoints_api.py's conventions.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import authz  # noqa: E402
import main as engine_api  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prompt_template_store import InMemoryBackend, PromptTemplateStore  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


@pytest.fixture()
def pt_store():
    s = PromptTemplateStore(InMemoryBackend())
    engine_api.app.state.prompt_template_store = s
    yield s
    engine_api.app.state.prompt_template_store = None


ADMIN_HEADERS = {"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"}
RANDO_HEADERS = {"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "rando@x"}


def _setup_admin(monkeypatch):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x"})


def _mock_valid_review(monkeypatch):
    monkeypatch.setattr(engine_api.app_logic, "_client", lambda *a, **k: object())
    monkeypatch.setattr(engine_api.app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')


def _mock_broken_review(monkeypatch):
    monkeypatch.setattr(engine_api.app_logic, "_client", lambda *a, **k: object())
    monkeypatch.setattr(engine_api.app_logic, "_claude", lambda *a, **k: "não posso ajudar.")


# --- gating ---------------------------------------------------------------------------------

PT_GET_ENDPOINTS = ("/api/admin/prompt-template",)


@pytest.mark.parametrize("path", PT_GET_ENDPOINTS)
def test_prompt_template_403_a_non_admin(monkeypatch, pt_store, path):
    _setup_admin(monkeypatch)
    assert client.get(path, headers=RANDO_HEADERS).status_code == 403


def test_save_403_a_non_admin(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/prompt-template", json={"template_text": "x"}, headers=RANDO_HEADERS)
    assert r.status_code == 403


def test_endpoint_403_when_store_absent(monkeypatch):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/prompt-template", headers=ADMIN_HEADERS).status_code == 503


# --- get: default vs. custom ------------------------------------------------------------------


def test_get_with_no_custom_saved_shows_only_the_default(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    r = client.get("/api/admin/prompt-template", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["custom"] is None
    assert "Genie Reviewer" in body["default"]


def test_get_after_saving_shows_the_custom_value(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    _mock_valid_review(monkeypatch)
    client.post("/api/admin/prompt-template", json={"template_text": "Seja mais rigoroso."},
               headers=ADMIN_HEADERS)
    r = client.get("/api/admin/prompt-template", headers=ADMIN_HEADERS)
    assert r.json()["custom"]["template_text"] == "Seja mais rigoroso."
    assert r.json()["custom"]["updated_by"] == "admin@x"


# --- save: validated before it ever reaches the store -----------------------------------------


def test_save_a_valid_template_persists_it(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    _mock_valid_review(monkeypatch)
    r = client.post("/api/admin/prompt-template", json={"template_text": "Seja mais rigoroso."},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert pt_store.get().template_text == "Seja mais rigoroso."


def test_save_a_template_that_breaks_output_parsing_is_rejected_400(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    _mock_broken_review(monkeypatch)
    r = client.post("/api/admin/prompt-template", json={"template_text": "Ignore o formato JSON."},
                    headers=ADMIN_HEADERS)
    assert r.status_code == 400
    assert pt_store.get() is None  # rejected BEFORE it ever reached the store


# --- reset --------------------------------------------------------------------------------------


def test_reset_reverts_to_the_default(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    _mock_valid_review(monkeypatch)
    client.post("/api/admin/prompt-template", json={"template_text": "custom"}, headers=ADMIN_HEADERS)
    r = client.post("/api/admin/prompt-template/reset", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert client.get("/api/admin/prompt-template", headers=ADMIN_HEADERS).json()["custom"] is None


def test_reset_is_idempotent_not_404(monkeypatch, pt_store):
    _setup_admin(monkeypatch)
    r = client.post("/api/admin/prompt-template/reset", headers=ADMIN_HEADERS)
    assert r.status_code == 200

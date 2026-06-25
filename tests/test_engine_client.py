"""Unit tests for the Streamlit -> engine API HTTP client (AK4). Fakes requests; no network."""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return _FakeResp(self.payload)

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "json": json})
        return _FakeResp(self.payload)


def _load(monkeypatch, url, payload=None):
    monkeypatch.setenv("APP_ENGINE_URL", url)
    import engine_client
    importlib.reload(engine_client)
    fake = _FakeRequests(payload)
    monkeypatch.setattr(engine_client, "requests", fake)
    return engine_client, fake


def test_enabled_toggles_on_env(monkeypatch):
    monkeypatch.delenv("APP_ENGINE_URL", raising=False)
    import engine_client
    importlib.reload(engine_client)
    assert engine_client.enabled() is False
    monkeypatch.setenv("APP_ENGINE_URL", "https://x")
    importlib.reload(engine_client)
    assert engine_client.enabled() is True


def test_list_spaces_forwards_bearer_and_parses(monkeypatch):
    ec, fake = _load(monkeypatch, "https://engine.example",
                     {"spaces": [{"space_id": "a", "title": "X"}]})
    out = ec.list_spaces("tok-123")
    assert out == [{"space_id": "a", "title": "X"}]
    assert fake.calls[0]["url"] == "https://engine.example/spaces"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-123"


def test_review_posts_space_id_and_token(monkeypatch):
    ec, fake = _load(monkeypatch, "https://engine.example",
                     {"findings": [], "gate": {"conclusion": "failure"}})
    out = ec.review_space("sid-9", "tok-r")
    assert out["gate"]["conclusion"] == "failure"
    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["url"] == "https://engine.example/review"
    assert fake.calls[0]["json"] == {"space_id": "sid-9"}
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer tok-r"


def test_no_token_omits_authorization(monkeypatch):
    ec, fake = _load(monkeypatch, "https://engine.example", {"spaces": []})
    ec.list_spaces(None)
    assert "Authorization" not in fake.calls[0]["headers"]


def test_trailing_slash_is_stripped(monkeypatch):
    ec, fake = _load(monkeypatch, "https://engine.example/", {"spaces": []})
    ec.list_spaces("t")
    assert fake.calls[0]["url"] == "https://engine.example/spaces"


def test_refuses_to_forward_token_over_http(monkeypatch):
    ec, _ = _load(monkeypatch, "http://insecure.example", {"spaces": []})
    with pytest.raises(RuntimeError):
        ec.list_spaces("tok")
    with pytest.raises(RuntimeError):
        ec.review_space("sid", "tok")

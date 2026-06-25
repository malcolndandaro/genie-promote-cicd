"""HTTP client for the engine API (AK4).

Lets the Streamlit app call the engine over HTTP (the engine API app) instead of in-process,
forwarding the signed-in user's OBO token as a Bearer so the engine still acts as the user.
This proves the cross-app contract that AppKit (AK5) will use. Behind APP_ENGINE_URL: when it's
unset the app falls back to the in-process engine, so the working app is never at risk.
"""
from __future__ import annotations

import os

import requests

ENGINE_URL = os.environ.get("APP_ENGINE_URL", "").rstrip("/")
_TIMEOUT_LIST = 30
_TIMEOUT_REVIEW = 180  # the review runs an LLM call


def enabled() -> bool:
    """True when the app should call the engine API (APP_ENGINE_URL set) vs in-process."""
    return bool(ENGINE_URL)


def _require_https() -> None:
    """Never forward the user's OBO Bearer token over cleartext."""
    if not ENGINE_URL.startswith("https://"):
        raise RuntimeError(
            "APP_ENGINE_URL must be https — refusing to forward the OBO token over cleartext.")


def _headers(user_token: str | None) -> dict:
    h = {"Content-Type": "application/json"}
    if user_token:  # forward the user's OBO token to the engine app (it acts as the user)
        h["Authorization"] = f"Bearer {user_token}"
    return h


def list_spaces(user_token: str | None) -> list[dict]:
    _require_https()
    r = requests.get(f"{ENGINE_URL}/spaces", headers=_headers(user_token), timeout=_TIMEOUT_LIST)
    r.raise_for_status()
    return r.json().get("spaces", [])


def review_space(space_id: str, user_token: str | None) -> dict:
    _require_https()
    r = requests.post(f"{ENGINE_URL}/review", json={"space_id": space_id},
                      headers=_headers(user_token), timeout=_TIMEOUT_REVIEW)
    r.raise_for_status()
    return r.json()

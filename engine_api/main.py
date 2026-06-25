"""FastAPI "engine API" — a thin HTTP boundary over the Python review engine (AK1).

Reuses app/app_logic.py + genie_reviewer/* UNCHANGED; it only adds transport + OBO plumbing,
so the frontend (Streamlit today, AppKit next) can be a mere client of the same engine.

OBO: the user token is read from EITHER the proxy-injected `x-forwarded-access-token` header
(a direct/proxied call) OR an `Authorization: Bearer <token>` header (an app-to-app call from a
frontend), and threaded into the engine as `user_token=` (the engine's `_client` OBO path).
This dual-source rule is what lets both the proxy path and the cross-app path work.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# Reuse the existing engine unchanged: app_logic resolves genie_reviewer/ + scripts/ itself.
# NB: this relies on the app being deployed with source_code_path = the REPO ROOT (so app/ and
# genie_reviewer/ are present). AK2 must keep that invariant — a subdir source path breaks these
# relative sys.path inserts.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "app"))
import app_logic  # noqa: E402

logger = logging.getLogger("engine_api")
app = FastAPI(title="genie-promote engine API")


def _engine_call(label: str, fn: Callable):
    """Run an engine call; map any failure to a 502 with a generic body (no internal/SDK detail
    leaked to the client) and log the real error server-side. The error contract every endpoint
    shares — a bad/expired OBO token is the common case and must never be an unhandled crash."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        logger.exception("engine call failed: %s", label)
        raise HTTPException(status_code=502, detail=f"engine error: {label}")


def _user_token(x_forwarded_access_token: str | None, authorization: str | None) -> str | None:
    """OBO token: prefer the proxy-injected header; fall back to an app-to-app Bearer.

    The proxy-injected `x-forwarded-access-token` wins because the Databricks Apps proxy strips
    and re-injects `x-forwarded-*` (a client can't spoof it on a proxied request); the Bearer is
    only for trusted app-to-app calls.
    """
    if x_forwarded_access_token:
        return x_forwarded_access_token
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[len("bearer "):].strip() or None
    return None


@app.get("/health")
def health() -> dict:
    """Liveness — no auth."""
    return {"status": "ok"}


@app.get("/whoami")
def whoami(
    x_forwarded_email: str | None = Header(default=None),
    x_forwarded_user: str | None = Header(default=None),
) -> dict:
    """The caller identity the platform forwards (OBO context). These headers are
    proxy-trust-only — informational, never an authorization input. Any security decision
    (e.g. AK8 Steward SoD) must use the OBO-token-resolved identity, not these raw headers."""
    return {"email": x_forwarded_email, "user": x_forwarded_user}


@app.get("/spaces")
def spaces(
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """The caller's Genie spaces, fetched on behalf of the user (OBO)."""
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")
    return {"spaces": _engine_call("list_spaces", lambda: app_logic.list_spaces(user_token=token))}


class ReviewRequest(BaseModel):
    space_id: str


# Only the UI-facing fields of the review result (drop the large prod_serialized payload).
_REVIEW_FIELDS = ("findings", "gate", "eval", "timeline", "allowlist_violations", "consumer_group")


@app.post("/review")
def review(
    body: ReviewRequest,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """The full promotion review for a space (export via OBO -> pre-render -> ENV-01 + grant
    preview + LLM reviewer + eval gate). The Genie export runs on behalf of the user; the LLM
    reviewer + grant preview run as the app SP (the engine's existing auth split)."""
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")

    def _run() -> dict:
        result = app_logic.review_space(body.space_id, user_token=token)
        # result[k] (not .get): if the engine ever drops a field, surface it as a 502 here
        # rather than silently emitting null and breaking the UI with no server-side signal.
        return {k: result[k] for k in _REVIEW_FIELDS}

    return _engine_call("review_space", _run)

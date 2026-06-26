"""FastAPI app — the single front door: a thin HTTP boundary over the Python review engine that
ALSO serves the built Svelte SPA from the same origin (SV1).

Reuses app/app_logic.py + genie_reviewer/* UNCHANGED; it only adds transport + OBO plumbing +
static SPA serving. Because the SPA and API share one app/origin, the Databricks Apps proxy
injects the user's `x-forwarded-access-token` directly into THIS app's requests — OBO is read
**in-process** (no cross-app Bearer hop).

Routing:
  - The JSON API is mounted at BOTH `/` (legacy engine-API contract: `/health`, `/whoami`,
    `/spaces`, `/review`) AND `/api/*` (what the SPA calls). One `APIRouter`, two mounts.
  - A catch-all serves the SPA's `index.html` for non-API GET routes (deep links) and static
    assets from the build dir. It never shadows the API namespace.

OBO: the user token is read from EITHER the proxy-injected `x-forwarded-access-token` header OR an
`Authorization: Bearer <token>` header, and threaded into the engine as `user_token=` (the
engine's `_client` OBO path). In the single-app design the proxy header is the live path; the
Bearer branch is retained only for transitional app-to-app callers and tests.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Callable

from databricks.sdk.errors import PermissionDenied, Unauthenticated
from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

# Reuse the existing engine unchanged: app_logic resolves genie_reviewer/ + scripts/ itself.
# NB: this relies on the app being deployed with the engine package (app/ + genie_reviewer/)
# alongside engine_api/ — the build assembly (scripts/build_*_app.sh) guarantees that layout.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "app"))
import app_logic  # noqa: E402
import promotion_store  # noqa: E402  (Lakebase durable store — LB2)
import reconcile as reconcile_mod  # noqa: E402  (GitHub->audit reconcile — LB4)
from github_app import GitHubError  # noqa: E402  (genie_reviewer is on sys.path via app_logic)

logger = logging.getLogger("engine_api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialize the Lakebase store at startup. Lakebase is a HARD dependency of the deployed app
    (ADR-0005): if the `database` binding injected PGHOST but the DB can't be reached/migrated,
    `build_store_from_env` raises `LakebaseUnavailable` and the app fails to start — fast + loud, no
    silent half-broken app. Locally/in tests (no PGHOST) the store is None and the app still runs.
    Idempotent startup migrations create the schema, so a fresh deployment self-initializes."""
    app.state.store = promotion_store.build_store_from_env()  # raises (fail-fast) if Lakebase is down
    logger.info("Lakebase store: %s",
                "initialized (migrations applied)" if app.state.store else "absent (no PGHOST — local/test)")
    try:
        yield
    finally:
        if app.state.store is not None:
            app.state.store.close()  # release the connection pool on graceful shutdown


app = FastAPI(title="genie-promote app", lifespan=_lifespan)
# Default so requests before/without lifespan (e.g. module-level TestClient) see a defined attribute.
app.state.store = None
api = APIRouter()


def _engine_call(label: str, fn: Callable):
    """Run an engine call and map failures to the right HTTP status.

    A bad/expired OBO token (or an insufficient grant) is the USER's problem — re-auth fixes it —
    so it surfaces as 401/403, letting the client prompt a reload instead of rendering a generic
    outage. Everything else is a 502 with a generic body (no internal/SDK detail leaked) and the
    real error is logged server-side."""
    try:
        return fn()
    except Unauthenticated:
        logger.info("OBO auth failed in %s", label)
        raise HTTPException(status_code=401, detail="Sessão expirada — recarregue para reautenticar.")
    except PermissionDenied:
        logger.info("OBO permission denied in %s", label)
        raise HTTPException(status_code=403, detail="Sem permissão para este recurso (OBO).")
    except GitHubError:
        # The bot couldn't reach GitHub / the App isn't installed/authorized — actionable, not a bug.
        logger.exception("GitHub op failed in %s", label)
        raise HTTPException(status_code=503, detail="Integração GitHub indisponível — verifique a instalação do app.")
    except Exception:  # noqa: BLE001
        logger.exception("engine call failed: %s", label)
        raise HTTPException(status_code=502, detail=f"engine error: {label}")


def _allow_bearer() -> bool:
    """Whether an `Authorization: Bearer` OBO fallback is honored. OFF by default — the single
    front-door app is same-origin (the proxy always injects `x-forwarded-access-token`), so the
    Bearer branch is pure attack surface there. Only the legacy engine-only API (proxied to by the
    AppKit app until the SV5 cutover) sets `APP_ALLOW_BEARER_OBO=1`. Read per-request so tests can
    toggle it."""
    return os.environ.get("APP_ALLOW_BEARER_OBO") == "1"


def _user_token(x_forwarded_access_token: str | None, authorization: str | None) -> str | None:
    """OBO token: the proxy-injected `x-forwarded-access-token` (the live path) wins because the
    Databricks Apps proxy strips and re-injects `x-forwarded-*` (a client can't spoof it on a
    proxied request). An `Authorization: Bearer` fallback is honored ONLY when explicitly enabled
    (`_allow_bearer`) — for the transitional app-to-app engine API and tests."""
    if x_forwarded_access_token:
        return x_forwarded_access_token
    if _allow_bearer() and authorization and authorization.lower().startswith("bearer "):
        return authorization[len("bearer "):].strip() or None
    return None


@api.get("/health")
def health() -> dict:
    """Liveness — no auth."""
    return {"status": "ok"}


def _admin_emails() -> set[str]:
    """The config-driven set of Steward/Admin emails who may see ALL promotions (LB5) — lowercased.
    Sourced from `APP_ADMINS` + `APP_STEWARDS` (comma-separated) + the single `APP_STEWARD` (SoD).
    NO hardcoded identities (ADR-0004): a new customer sets these env vars + redeploys."""
    raw = ",".join(os.environ.get(k, "") for k in ("APP_ADMINS", "APP_STEWARDS", "APP_STEWARD"))
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _is_admin(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in _admin_emails()


@api.get("/whoami")
def whoami(x_forwarded_email: str | None = Header(default=None)) -> dict:
    """The caller identity the platform forwards (OBO context) + the configured Steward + whether the
    caller is an Admin/Steward (drives the LB5 `scope=all` history toggle).

    `email` comes from `x-forwarded-email` (proxy-trust-only — display only, NEVER an
    authorization input); `steward` is the `APP_STEWARD` config (the distinct approver for the
    separation-of-duties model in SV4); `is_admin` reflects the config-driven admin/steward set.
    Any security decision must use the OBO-token-resolved identity, not these raw headers — the
    server re-checks the role on `scope=all` (this flag is only a UI convenience).
    """
    return {"email": x_forwarded_email, "steward": os.environ.get("APP_STEWARD") or None,
            "is_admin": _is_admin(x_forwarded_email)}


@api.get("/spaces")
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


@api.post("/review")
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


class PromoteRequest(BaseModel):
    space_id: str
    # Display metadata the SPA already holds (from /api/spaces) — persisted with the Promotion so the
    # history/recovery view (LB3/LB5) shows the resource without a second OBO lookup. Optional so the
    # legacy {space_id}-only contract still works.
    resource_title: str | None = None
    resource_kind: str | None = None


def _persist_promotion(store, result: dict, body: PromoteRequest, requester_email: str | None) -> str:
    """Persist a promotion request (LB3): the Promotion is 1:1 with the PR — a FIRST request creates
    it + a `requested` audit event; re-requesting while the PR is open adds a NEW snapshot +
    `re_reviewed` to the SAME Promotion (no fragmentation). Every request appends a Review Snapshot
    (the reviewer result at that point in time) so recovery renders it WITHOUT re-running the LLM.
    DB writes are the app SP's job (this runs server-side), preserving the OBO/SP split."""
    from promotion_store import DuplicatePRNumber

    review, pr = result["review"], result["pr"]
    existing = store.find_by_pr(pr["number"])
    if existing is None:
        try:
            promotion = store.create_promotion(
                resource_id=body.space_id, resource_kind=body.resource_kind or "genie_space",
                resource_title=body.resource_title, requester_email=requester_email,
                pr_number=pr["number"], pr_url=pr["url"], branch=app_logic.GH_PROMOTION_BRANCH,
                current_phase="open", live_status=None)
            promotion_id, event = promotion.id, "requested"
        except DuplicatePRNumber:
            # Concurrent request created it between our find_by_pr and create (TOCTOU). Recover by
            # treating this as a re-review of the now-existing Promotion rather than 500-ing.
            existing = store.find_by_pr(pr["number"])
            if existing is None:
                raise
            promotion_id, event = existing.id, "re_reviewed"
    else:
        promotion_id, event = existing.id, "re_reviewed"
    gate = review.get("gate") or {}
    store.append_snapshot(
        promotion_id, gate_conclusion=gate.get("conclusion"), gate_summary=gate.get("summary"),
        findings=review.get("findings") or [], eval=review.get("eval"),
        timeline=review.get("timeline") or [])
    # App-originated event. `actor_app_email` (display-only OBO email) is recorded on `requested`
    # per ADR-0005; governance identities come from GitHub via reconcile (LB4). On a re-review the
    # Promotion's updated_at is bumped so freshness reflects the activity before the next reconcile.
    store.append_audit_event(
        promotion_id, event,
        actor_app_email=requester_email if event == "requested" else None,
        detail={"pr_number": pr["number"], "pr_url": pr["url"]})
    if event == "re_reviewed":
        store.touch(promotion_id)
    return promotion_id


@api.post("/promote")
def promote(
    body: PromoteRequest,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_forwarded_email: str | None = Header(default=None),
) -> dict:
    """GH2 + LB3: open (or update) a real promotion PR for a space, post the attributed review
    comment, AND persist the Promotion + Review Snapshot + audit event. Reviews as the user (OBO) +
    reviewer SP; opens the PR + comments as the GitHub App (bot); DB writes as the app SP. The human
    requester (x-forwarded-email, display-only) is attributed. Returns `{review, pr, promotion_id?}`."""
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")
    result = _engine_call(
        "request_promotion",
        lambda: app_logic.request_promotion(
            body.space_id, user_token=token, requester_email=x_forwarded_email),
    )
    store = getattr(app.state, "store", None)
    if store is not None:  # deployed app always has it (hard dep); local-without-Lakebase skips
        result["promotion_id"] = _persist_promotion(store, result, body, x_forwarded_email)
    return result


@api.get("/promote/{number}/status")
def promote_status(number: int) -> dict:
    """GH3 + LB4: live status of a promotion PR (checks + merge + prod deploy/gate), read as the BOT
    (app SP). On each read it RECONCILES the persisted promotion against this live status — appending
    any newly-reached audit events (GitHub-sourced identities) + refreshing the cached status — so
    the audit trail self-heals + a reload renders the cached status instantly. Reflect, never assert."""
    status = _engine_call("promotion_status", lambda: app_logic.promotion_status(number))
    store = getattr(app.state, "store", None)
    if store is not None:
        promotion = store.find_by_pr(number)
        if promotion is not None:
            # github_factory is lazy: reconcile builds the bot only if it must attribute a new
            # merge/approval (a transition), so the 5s poll pays nothing extra on a steady state.
            try:
                reconcile_mod.reconcile(store, promotion, status, app_logic.github_app_factory)
            except Exception:  # noqa: BLE001 — a reconcile hiccup must not break the status read
                logger.exception("reconcile failed for PR #%s", number)
    return status


# --- LB3: durable history + recover-on-open (no reviewer re-run) ------------


def _require_store():
    store = getattr(app.state, "store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Persistência (Lakebase) indisponível.")
    return store


def _promotion_summary(p) -> dict:
    return {"id": p.id, "resource_id": p.resource_id, "resource_kind": p.resource_kind,
            "resource_title": p.resource_title, "requester_email": p.requester_email,
            "pr_number": p.pr_number, "pr_url": p.pr_url,
            "current_phase": p.current_phase, "terminal": p.terminal,
            "created_at": p.created_at, "updated_at": p.updated_at}


def _audit_event(e) -> dict:
    """Serialize an audit event for the SPA trail (LB4). `actor_github_login` is the AUTHORITATIVE
    governance identity; `actor_app_email` is display-only (present only on `requested`)."""
    return {"seq": e.seq, "event_type": e.event_type, "occurred_at": e.occurred_at,
            "actor_github_login": e.actor_github_login, "actor_app_email": e.actor_app_email,
            "github_event_at": e.github_event_at, "detail": e.detail}


def _snapshot_to_review(snap) -> dict:
    """Reconstruct the UI Review from a stored snapshot. blocker_count is derived from findings;
    allowlist_violations/consumer_group aren't part of the rendered review (recomputed in CI), so
    they default — the recovered review renders identically without re-running the reviewer."""
    findings = snap.findings or []
    return {
        "findings": findings,
        "gate": {"conclusion": snap.gate_conclusion, "summary": snap.gate_summary,
                 "blocker_count": sum(1 for f in findings if f.get("severity") == "BLOCKER")},
        "eval": snap.eval or {"status": "n/a", "summary": "eval-run indisponível"},
        "timeline": snap.timeline or [],
        "allowlist_violations": [], "consumer_group": "",
    }


@api.get("/promotions")
def list_promotions(scope: str = "mine", x_forwarded_email: str | None = Header(default=None)) -> dict:
    """The caller's promotions (newest first), for recovery + the history view (LB3/LB5).
    `scope=mine` filters by the OBO email (convenience; the proxy is the access boundary).
    `scope=all` (cross-user governance view) is ROLE-GATED server-side: only a configured
    Steward/Admin gets it; anyone else is denied (403). Bot/SP owns the DB read."""
    store = _require_store()
    if scope == "mine":
        promotions = store.list_promotions(x_forwarded_email)  # None email (local) => all
    elif scope == "all":
        if not _is_admin(x_forwarded_email):
            raise HTTPException(status_code=403, detail="Apenas Stewards/Admins veem todas as promoções.")
        promotions = store.list_promotions(None)  # cross-user
    else:
        raise HTTPException(status_code=400, detail="scope inválido; use 'mine' ou 'all'")
    return {"promotions": [_promotion_summary(p) for p in promotions]}


def _visible_promotion_or_403(store, promotion_id: str, email: str | None):
    """Fetch a promotion the caller may see: their own (requester_email == OBO email) OR any if the
    caller is a Steward/Admin. Defense-in-depth over the proxy boundary — a user can't read another
    user's stored review/audit by guessing an id (they only ever learn their own ids via scope=mine)."""
    p = store.get_promotion(promotion_id)
    if p is None:
        raise HTTPException(status_code=404, detail="promoção não encontrada")
    # No proxy email == local/dev (the deployed proxy always injects it) -> allow. Otherwise the
    # caller must own the promotion or be an admin.
    if email is None or _is_admin(email) or (
            p.requester_email and p.requester_email.lower() == email.lower()):
        return p
    raise HTTPException(status_code=403, detail="Sem permissão para esta promoção.")


@api.get("/promotions/{promotion_id}")
def get_promotion(promotion_id: str, x_forwarded_email: str | None = Header(default=None)) -> dict:
    """A single promotion + its LATEST stored Review Snapshot + PR ref — the recover-on-open payload
    (renders the stored review WITHOUT re-running the LLM reviewer). Live PR/CI/deploy status still
    comes from the GitHub poll (`/promote/{n}/status`), unchanged. Caller must own it or be an admin."""
    store = _require_store()
    p = _visible_promotion_or_403(store, promotion_id, x_forwarded_email)
    snap = store.latest_snapshot(promotion_id)
    return {
        "promotion": _promotion_summary(p),
        "review": _snapshot_to_review(snap) if snap else None,
        "pr": {"number": p.pr_number, "url": p.pr_url} if p.pr_number else None,
        # The cached status lets the SPA render the phase badge IMMEDIATELY on load (LB4), before the
        # first live poll lands. The audit trail (append-only, GitHub-attributed) drives the trail view.
        "live_status": p.live_status,
        "audit": [_audit_event(e) for e in store.list_audit_events(promotion_id)],
    }


@api.get("/promotions/{promotion_id}/audit")
def get_promotion_audit(promotion_id: str, x_forwarded_email: str | None = Header(default=None)) -> dict:
    """The ordered, append-only audit trail for a promotion (LB4) — refreshed by the SPA as the
    status poll reconciles, so the trail accrues live as the PR progresses. Owner-or-admin only."""
    store = _require_store()
    _visible_promotion_or_403(store, promotion_id, x_forwarded_email)
    return {"audit": [_audit_event(e) for e in store.list_audit_events(promotion_id)]}


# Mount the API twice: at root (legacy engine-API contract) and under /api (what the SPA calls).
app.include_router(api)
app.include_router(api, prefix="/api")


# --- Static SPA serving (must be registered AFTER the API so it never shadows it) ---

# Paths that belong to the JSON API surface; an unmatched one is a 404, NOT the SPA shell.
_API_PATHS = {"health", "whoami", "spaces", "review"}


def _has_index(d: str | None) -> bool:
    return bool(d) and os.path.isfile(os.path.join(d, "index.html"))


def _static_dir() -> str | None:
    """Resolve the built Svelte SPA directory (the one containing index.html), or None.

    Read lazily (per request, cheap). `APP_STATIC_DIR` is AUTHORITATIVE when set (tests/overrides
    use exactly that dir, no fallback). Otherwise: the assembled app's `static/` (deploy) or
    `web/dist` (local build).
    """
    override = os.environ.get("APP_STATIC_DIR")
    if override is not None:
        return override if _has_index(override) else None
    here = os.path.dirname(os.path.abspath(__file__))      # .../engine_api
    approot = os.path.dirname(here)                          # assembled app root OR repo root
    for d in (os.path.join(approot, "static"), os.path.join(approot, "web", "dist")):
        if _has_index(d):
            return d
    return None


@app.get("/{full_path:path}")
def spa(full_path: str):
    """Serve the SPA: a real static file if the path maps to one, else index.html (SPA deep
    links). Never serves the SPA for the API namespace (`/api/*` or the legacy API paths) — an
    unmatched API path is a JSON 404 so the API contract stays clean."""
    if full_path.startswith("api/") or full_path in _API_PATHS:
        return JSONResponse({"detail": "not found"}, status_code=404)

    sdir = _static_dir()
    if not sdir:
        # The SPA build isn't present (e.g. a backend-only test run). Don't 500 — return a
        # minimal placeholder so the app is still live.
        return HTMLResponse(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Promotor de Genie/AI-BI</title></head>"
            "<body><p>SPA build not found — run the web build.</p></body></html>"
        )

    if full_path:
        candidate = os.path.realpath(os.path.join(sdir, full_path))
        if candidate.startswith(os.path.realpath(sdir) + os.sep) and os.path.isfile(candidate):
            # Vite emits content-hashed assets/* — safe to cache forever (immutable).
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"})
    # The SPA shell must NOT be cached, or a redeploy leaves clients pinned to stale asset hashes.
    return FileResponse(os.path.join(sdir, "index.html"), headers={"Cache-Control": "no-cache"})

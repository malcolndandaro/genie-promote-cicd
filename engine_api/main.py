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

Authorization identity (A2): every authz decision in this module (admin/Steward gating, per-user
promotion visibility) is derived from the platform-VERIFIED identity behind the OBO token
(`authz.verify_identity`), never from the client/proxy-supplied `x-forwarded-email` header. That
header is retained ONLY as display metadata (whoami's `email` field, audit attribution) — see
`authz.py` for why a header, even a proxy-injected one, is not treated as an authorization input
here. `_verified_email` below is the ONE place a request's verified identity is resolved; it fails
closed (returns None -> "not admin, not this promotion's owner") on a missing/invalid token rather
than raising, because most of these endpoints must still degrade gracefully for local/offline
callers with no OBO token in play (bare `profile`-based local runs) — the None case is handled by
every caller as "no elevated access", never as "skip the check".
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Callable

from databricks.sdk.core import Config
from databricks.sdk.errors import PermissionDenied, Unauthenticated
from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

# Reuse the existing engine unchanged: app_logic resolves genie_reviewer/ + scripts/ itself.
# NB: this relies on the app being deployed with the engine package (app/ + genie_reviewer/)
# alongside engine_api/ — the build assembly (scripts/build_*_app.sh) guarantees that layout.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "app"))
import app_logic  # noqa: E402  (MUST import before access_spec/github_app — it puts genie_reviewer/ on sys.path)
import access_spec as access_spec_mod  # noqa: E402  (F2 — declared-access model)
import authz  # noqa: E402  (A2 — verified identity; the ONLY legitimate authz input)
import access_request_store  # noqa: E402  (F3 — self-service access requests: store + audit)
import admin_inventory  # noqa: E402  (F4 — live-prod-inventory join, pure/offline-testable)
import promotion_store  # noqa: E402  (Lakebase durable store — LB2)
import reconcile as reconcile_mod  # noqa: E402  (GitHub->audit reconcile — LB4)
import rehydrate as rehydrate_mod  # noqa: E402  (A3/F1 — prod->dev rehydrate, no git PR)
import roles_store  # noqa: E402  (F5 — configurable roles store, store-over-env precedence)
import rules_store  # noqa: E402  (G2 — admin-configurable reviewer rules, safe hardcoded fallback)
from github_app import GitHubError  # noqa: E402  (genie_reviewer is on sys.path via app_logic)
import github_drift  # noqa: E402  (F5 — READ-ONLY GitHub gate drift detection)
import handbook_rules  # noqa: E402  (G2 — the hardcoded rule_ids a plain override must reference)
import rules_config  # noqa: E402  (G2 — the pure hardcoded+overrides merge, for GET /admin/rules)

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
    # F3: the access-request store — independent tables/pool, same hard-dependency contract (fails
    # fast + loud if PGHOST is set but unreachable; None locally/offline with no PGHOST).
    app.state.access_store = access_request_store.build_store_from_env()
    logger.info("Access-request store: %s",
                "initialized (migrations applied)" if app.state.access_store else "absent (no PGHOST — local/test)")
    # F5: the roles store — Steward/Admin/approver + email<->GitHub-username mapping, same
    # hard-dependency contract. `whoami`/`_is_admin` read it with the env vars as a bootstrap
    # fallback (see `_admin_emails`/`_steward_emails` below) so a fresh install with an empty store
    # keeps working exactly as before F5.
    app.state.roles_store = roles_store.build_store_from_env()
    logger.info("Roles store: %s",
                "initialized (migrations applied)" if app.state.roles_store else "absent (no PGHOST — local/test)")
    # G2: the admin-configurable rules store — same hard-dependency contract. `review()` reads it
    # with `handbook_rules.RULES` as the safe fallback (see `rules_config.py`), so a fresh install
    # with an empty/absent store reviews EXACTLY as before this slice shipped.
    app.state.rules_store = rules_store.build_store_from_env()
    logger.info("Rules store: %s",
                "initialized (migrations applied)" if app.state.rules_store else "absent (no PGHOST — local/test)")
    reconciler = _start_scheduled_reconciler(app.state.store)  # LB6 — None when disabled/no store
    try:
        yield
    finally:
        if reconciler is not None:
            reconciler.cancel()
            try:
                await reconciler  # drain any in-flight sweep BEFORE closing the pool it uses
            except asyncio.CancelledError:
                pass
        if app.state.store is not None:
            app.state.store.close()  # release the connection pool on graceful shutdown
        if app.state.access_store is not None:
            app.state.access_store.close()
        if app.state.roles_store is not None:
            app.state.roles_store.close()
        if app.state.rules_store is not None:
            app.state.rules_store.close()


def _start_scheduled_reconciler(store):
    """LB6: a config-driven in-app periodic reconciler. We use the app's own task (not a separate
    DABs job) because it REUSES the app's live store + bot factory + injected PG*/secret env with
    ZERO connection re-plumbing — a job wouldn't get the app's `database` binding injection and would
    have to re-resolve the Lakebase connection. The Databricks Apps container runs continuously (not
    scale-to-zero), so a periodic task is reliable. Enabled + paced by `APP_RECONCILE_INTERVAL_SEC`
    (>0); disabled when 0/unset or no store. Idempotent (reuses LB4 reconcile), so a multi-replica
    deploy is safe (the partial unique index dedups)."""
    try:
        interval = int(os.environ.get("APP_RECONCILE_INTERVAL_SEC", "0"))
    except ValueError:
        interval = 0
    if store is None or interval <= 0:
        logger.info("Scheduled reconciler: disabled (APP_RECONCILE_INTERVAL_SEC=%s, store=%s)",
                    os.environ.get("APP_RECONCILE_INTERVAL_SEC"), store is not None)
        return None
    floor = 60  # guard a misconfigured tiny interval from hammering the GitHub API budget
    if interval < floor:
        logger.warning("APP_RECONCILE_INTERVAL_SEC=%s below the %ss floor — clamping.", interval, floor)
        interval = floor

    async def _loop():
        logger.info("Scheduled reconciler: every %ss over non-terminal promotions", interval)
        while True:
            await asyncio.sleep(interval)
            try:
                # Run the blocking DB+HTTP sweep off the event loop.
                result = await asyncio.to_thread(
                    reconcile_mod.reconcile_all, store, app_logic.github_app_factory, logger)
                if result["transitioned"]:
                    logger.info("Scheduled reconcile: %s checked, transitions=%s",
                                result["checked"], result["transitioned"])
            except Exception:  # noqa: BLE001 — never let the loop die on one bad sweep
                logger.exception("scheduled reconcile sweep failed")

    return asyncio.create_task(_loop())


app = FastAPI(title="genie-promote app", lifespan=_lifespan)
# Default so requests before/without lifespan (e.g. module-level TestClient) see a defined attribute.
app.state.store = None
app.state.access_store = None  # F3
app.state.roles_store = None  # F5
app.state.rules_store = None  # G2
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
    except authz.AccessDenied as e:
        # A2's live, fail-closed guard denied the VERIFIED caller — a normal 403, distinct from the
        # dev-bootstrap case below (SP2's disambiguation: a genuinely-denied user must never see a
        # "re-bootstrap" prompt).
        logger.info("access denied in %s (%s)", label, e)
        raise HTTPException(status_code=403, detail="Sem permissão para este recurso.")
    except rehydrate_mod.DevEnvironmentNotBootstrapped as e:
        # The dev-reader/writer SP itself is unreachable/ungranted (SP2) — actionable + DISTINCT
        # from a 403: the caller isn't denied, the dev environment needs a re-bootstrap.
        logger.warning("dev environment not bootstrapped in %s (%s)", label, e)
        raise HTTPException(status_code=503, detail=str(e))
    except access_request_store.SelfApprovalError as e:
        # F3 SoD: a caller tried to approve/deny their own access request — server-enforced,
        # never just a UI affordance. A plain 403, distinct from authz.AccessDenied (that's about
        # RESOURCE access; this is about WHO may decide on THIS request).
        logger.info("self-approval rejected in %s (%s)", label, e)
        raise HTTPException(status_code=403, detail=str(e))
    except access_request_store.InvalidTransition as e:
        logger.info("invalid access-request transition in %s (%s)", label, e)
        raise HTTPException(status_code=409, detail=str(e))
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


def _env_emails(*keys: str) -> frozenset[str]:
    """The comma-separated emails in the given env vars, lowercased — the F5 BOOTSTRAP FALLBACK
    (only consulted when the roles store is empty; see `_roles_store` callers below)."""
    raw = ",".join(os.environ.get(k, "") for k in keys)
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def _roles_store():
    return getattr(app.state, "roles_store", None)


def _steward_emails() -> frozenset[str]:
    """The effective set of Steward emails (F5): the roles store is authoritative once it holds ANY
    role rows; `APP_STEWARDS` + `APP_STEWARD` (comma-separated) is the bootstrap fallback for a
    fresh/unconfigured store. NO hardcoded identities (ADR-0004)."""
    store = _roles_store()
    env_fallback = _env_emails("APP_STEWARDS", "APP_STEWARD")
    if store is None:
        return env_fallback
    return store.effective_emails("steward", env_fallback=env_fallback)


def _admin_emails() -> frozenset[str]:
    """The effective set of Steward/Admin emails who may see ALL promotions (LB5) + reach admin-
    gated endpoints — lowercased. F5: the roles store is authoritative (unioning its `admin` +
    `steward` role rows) once it holds ANY role rows at all; `APP_ADMINS` + `APP_STEWARDS` +
    `APP_STEWARD` (comma-separated) is the BOOTSTRAP FALLBACK for a fresh/unconfigured store — so
    an existing env-only deployment keeps working unchanged, and changing an approver in-app takes
    effect with NO redeploy once at least one role has been configured. Fails closed either way: an
    empty store AND empty/unset env vars yields an EMPTY set (`roles_store.effective_emails`'s own
    contract), never "everyone" and never a silently-open gate. NO hardcoded identities (ADR-0004)."""
    store = _roles_store()
    env_fallback = _env_emails("APP_ADMINS", "APP_STEWARDS", "APP_STEWARD")
    if store is None:
        return env_fallback
    admins = store.effective_emails("admin", env_fallback=env_fallback)
    stewards = store.effective_emails("steward", env_fallback=env_fallback)
    return admins | stewards


def _verified_email(x_forwarded_access_token: str | None, authorization: str | None) -> str | None:
    """Resolve the CALLER's platform-verified identity (email/user_name) for THIS request, from
    their OBO token — never from `x-forwarded-email`. This is the single replacement for the old
    (broken) control that authorized on the raw header.

    Returns None when no usable token is present or verification fails (missing token, expired/
    invalid token, dev unreachable) — callers MUST treat None as "no elevated access / not this
    resource's owner", i.e. fail closed, never as "trust the display header instead" or "skip the
    check". None also covers the legitimate local/offline case (no proxy, no token) where the
    caller has no verified identity to check against at all; those callers get baseline (non-admin,
    own-promotions-only-by-header-if-any) behavior, matching pre-A2 local-dev ergonomics.

    NOT cached beyond this one call: resolved fresh on every request from that request's own
    header, so a revoked/expired token can never authorize a subsequent request.
    """
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        return None
    try:
        identity = authz.verify_identity(token, host=Config().host)
    except authz.AccessDenied:
        logger.info("OBO token present but did not verify to an identity")
        return None
    return identity.user_name


def _is_admin(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in _admin_emails()


def _steward_display() -> str | None:
    """A single Steward email for `whoami`'s legacy `steward` field (SV4's SoD UI just needs ONE
    "the distinct approver" identity to compare the viewer against). F5: prefers the roles store's
    effective Steward set (deterministically the lexicographically-first email, so the value is
    stable across calls rather than an arbitrary set-iteration order); falls back to `APP_STEWARD`
    unchanged when the store is empty/absent, so existing single-Steward deployments see the exact
    same value as before F5."""
    stewards = sorted(_steward_emails())
    if stewards:
        return stewards[0]
    return os.environ.get("APP_STEWARD") or None


def _repo_url() -> str:
    """The source/CI repo the header links to. Config-driven (`APP_REPO_URL`) with the accelerator's
    own repo as the default — NO customer binding (ADR-0004): a fork sets its own URL + redeploys."""
    return os.environ.get("APP_REPO_URL") or "https://github.com/malcolndandaro/genie-promote-cicd"


@api.get("/whoami")
def whoami(
    x_forwarded_email: str | None = Header(default=None),
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """The caller identity the platform forwards (OBO context) + the configured Steward + whether the
    caller is an Admin/Steward (drives the LB5 `scope=all` history toggle).

    `email` is DISPLAY ONLY — sourced from `x-forwarded-email` purely for what the UI shows the
    user as "you are signed in as"; it is NEVER used to compute `is_admin` (A2: replaces the
    previous broken control, which authorized on this same header). `is_admin` is computed from the
    request's OWN platform-VERIFIED identity (`_verified_email`, resolved from the OBO token) against
    the config-driven admin/steward set — a caller with no valid OBO token verifies to no identity
    and is never admin, regardless of what `x-forwarded-email` claims. `steward` (F5: now
    store-backed, `APP_STEWARD`/`APP_STEWARDS` as the bootstrap fallback — see `_steward_display`)
    is the distinct approver for the separation-of-duties model (SV4). `dev_host` (G5) is the dev
    workspace host (`APP_DEV_HOST`, the same one `rehydrate.py` targets) — exposed purely so the SPA
    can build a deep-link to a just-rehydrated dev Space; `None` when unconfigured (local/offline).
    """
    verified = _verified_email(x_forwarded_access_token, authorization)
    return {"email": x_forwarded_email, "steward": _steward_display(),
            "is_admin": _is_admin(verified), "repo_url": _repo_url(),
            "dev_host": os.environ.get("APP_DEV_HOST")}


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


@api.get("/prod-spaces")
def prod_spaces(
    q: str = "",
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """G1: the id+title of every prod-deployed Genie Space, for the pickers that need to name a PROD
    space a caller may NOT already have access to (F3's "request access" flow is the whole reason a
    space id must be pickable without first proving access to it). Deliberately a thinner read than
    `/admin/inventory` (F4): title+id only, no owner/access/phase — so it's safe to gate the same as
    `/spaces` (any authenticated OBO caller) rather than admin-only. `q` filters server-side (a
    case-insensitive substring on title) — Genie's own API has no server-side search, so this reads
    the full live list fresh each call and filters here."""
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")

    def _run() -> dict:
        spaces = app_logic.list_prod_spaces()
        needle = q.strip().lower()
        if needle:
            spaces = [s for s in spaces if needle in (s.get("title") or "").lower()]
        return {"spaces": spaces}

    return _engine_call("list_prod_spaces", _run)


@api.get("/principals")
def principals(
    q: str = "",
    kind: str = "all",
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """G1: users + groups of the workspace directory, for every principal picker in the app (F2
    access declarations, F3 access requests, F5 role assignment) — the app must never accept a
    free-typed email/username as the SUBMITTED value; this is the one endpoint every such picker
    calls. Gated the same as `/spaces` (any authenticated OBO caller): directory listing isn't
    itself a security boundary here — the ACTION each picker feeds is separately gated (F2 is a
    declaration applied only via the governed pipeline; F3/F5 require admin approval server-side).

    TRANSPORT NOTE (found live): the SCIM read runs as the APP SP, not the caller's OBO token —
    the Apps-forwarded user token is DOWNSCOPED to the app's `user_api_scopes` (today only
    `dashboards.genie`), so SCIM `users.list` 403s on it ("Sem permissão para este recurso (OBO)").
    The OBO token still gates WHO may call; the SP is transport only, same split as `/spaces`."""
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")
    if kind not in ("all", "user", "group"):
        raise HTTPException(status_code=400, detail="kind inválido; use 'all', 'user' ou 'group'")
    return {"principals": _engine_call(
        "list_principals", lambda: app_logic.list_principals(q, kind=kind))}


class SpacePermissionIn(BaseModel):
    principal: str
    is_group: bool = False
    level: str = "CAN_RUN"


class PrincipalIn(BaseModel):
    principal: str
    is_group: bool = False


class AccessSpecIn(BaseModel):
    """F2: the Requester's declared access, over the wire. Mirrors
    `access_spec.AccessSpec.to_dict()` — kept as a distinct pydantic model (rather than a bare
    dict) so a malformed client payload 422s instead of failing deep inside the engine."""
    space_permissions: list[SpacePermissionIn] = []
    uc_principals: list[PrincipalIn] = []

    def to_engine(self) -> "access_spec_mod.AccessSpec":
        return access_spec_mod.AccessSpec.from_dict(self.model_dump())


class ReviewRequest(BaseModel):
    space_id: str
    # F2: the Requester's declared AccessSpec, previewed in the SAME review the Steward later sees
    # (GRANT-01 checks every declared principal; a PII-01 interplay note may be added) — optional so
    # the pre-F2 {space_id}-only contract still works and a review with no declared access is legal.
    access_spec: AccessSpecIn | None = None


# Only the UI-facing fields of the review result (drop the large prod_serialized payload).
_REVIEW_FIELDS = ("findings", "gate", "eval", "timeline", "allowlist_violations", "consumer_group",
                  "access_spec")


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
        spec = body.access_spec.to_engine() if body.access_spec else None
        # G2: the admin-configured rule overrides (None when no store — reviews with the hardcoded
        # handbook_rules.RULES, unchanged from before this slice).
        store = _rules_store()
        overrides = store.list_all_dicts() if store is not None else None
        result = app_logic.review_space(body.space_id, user_token=token, access_spec_=spec,
                                        rule_overrides=overrides)
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
    # F2: the same declared AccessSpec reviewed above, now carried through to the actual promotion
    # request — persisted with the Promotion + committed to the PR as a sidecar (declaration is
    # app-direct; enforcement is governed, see app_logic.request_promotion).
    access_spec: AccessSpecIn | None = None


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
                pr_number=pr["number"], pr_url=pr["url"], branch=result.get("branch", ""),
                current_phase="open", live_status=None,
                # F2: persist the declared AccessSpec WITH the Promotion (echoed back by
                # request_promotion's review payload) so it survives independently of the PR/branch
                # and renders in the recovery/history view without re-parsing the sidecar from git.
                access_spec=review.get("access_spec") or None)
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
    spec = body.access_spec.to_engine() if body.access_spec else None
    rules_store_ = _rules_store()
    overrides = rules_store_.list_all_dicts() if rules_store_ is not None else None
    result = _engine_call(
        "request_promotion",
        lambda: app_logic.request_promotion(
            body.space_id, user_token=token, requester_email=x_forwarded_email,
            resource_title=body.resource_title, access_spec_=spec, rule_overrides=overrides),
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


# --- A3/F1: prod->dev rehydrate (one-click reseed, no git PR) ---------------


class RehydrateRequest(BaseModel):
    source_prod_space_id: str
    mode: str  # "create" | "overwrite"
    dev_space_id: str | None = None  # required when mode == "overwrite"
    title: str | None = None
    # The Promotion this rehydrate is reseeding FROM (optional) — when given, the audit event is
    # attached to that Promotion's trail so "who rehydrated this deployed Space, and when" shows up
    # next to its promotion history, same place a Steward already looks.
    promotion_id: str | None = None


def _find_promotion_id_for_resource(store, resource_id: str) -> str | None:
    """G5: resolve which Promotion produced a live prod Space, by resource_id — mirrors
    `admin_inventory`'s id-match join (F4) but scoped to one id, no title fallback (rehydrate always
    has an EXACT space id to export, unlike the inventory's softer display-only join). Lets the
    standalone "Exportar para Dev" screen call `/rehydrate` with just the prod Space it picked from
    `getProdSpaces` (title+id only, no promotion_id) — RehydrateAction (started FROM a promotion's
    OWN detail) already knows its promotion_id and never needs this. Several Promotions can share a
    resource_id (re-promotions accumulate history); the most recently updated one wins. Returns None
    when no Promotion has ever targeted this resource — the caller no longer fails closed on that
    (stakeholder decision: rehydrate must work for any prod Space the caller can access); the
    endpoint falls through with `promotion_id=None`, and `rehydrate_space` records a standalone
    `rehydrate_events` row instead of a Promotion-linked one."""
    candidates = [p for p in store.list_promotions(None) if p.resource_id == resource_id]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.updated_at).id


@api.post("/rehydrate")
def rehydrate(
    body: RehydrateRequest,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """A3/F1: export a prod Space the caller can access and (re-)create it in dev, rebound
    `prod_`->`dev_` — no git PR. The source need NOT have been promoted through this app (the prod
    store starts empty per ADR-0006, so most prod Spaces have no Promotion row at all). Guarded at
    BOTH blast sites by `authz.assert_can_access` on the caller's platform-VERIFIED identity: the
    source prod Space (always) and, in `overwrite` mode, the target dev Space (so a caller can't
    clobber a dev Space they can't reach even though the standing dev-reader/writer SP could). A
    denial is a plain 403; an unreachable/unprovisioned dev SP (SP2) is a distinct 503 with an
    actionable re-bootstrap message — never confused with a 403.

    Non-repudiation (A3 acceptance + A3 review, revised): every rehydrate stays auditable when a
    durable store is present (prod), but via ONE of TWO paths (`rehydrate_space`/`_audit`) — a
    linkable source Promotion (preferred, when one exists) gets the richer `audit_events`
    "rehydrated" row; no match is no longer an error, it falls through with `promotion_id=None`
    and gets a standalone `rehydrate_events` row instead. An EXPLICITLY given `promotion_id` that
    doesn't exist is still a 404 (a dangling client reference, not "no promotion available").
    """
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")
    if body.mode not in ("create", "overwrite"):
        raise HTTPException(status_code=400, detail="mode deve ser 'create' ou 'overwrite'")
    if body.mode == "overwrite" and not body.dev_space_id:
        raise HTTPException(status_code=400, detail="overwrite requer dev_space_id")
    store = getattr(app.state, "store", None)
    promotion_id = body.promotion_id
    if store is not None:
        # G5: the standalone screen has no promotion_id to hand back (it picked a prod Space from
        # the plain getProdSpaces listing) — resolve it server-side; a linked trail is still
        # preferred when one exists, but finding none is no longer fatal (see the docstring above).
        if not promotion_id:
            promotion_id = _find_promotion_id_for_resource(store, body.source_prod_space_id)
        if promotion_id and store.get_promotion(promotion_id) is None:
            raise HTTPException(status_code=404, detail="promoção de origem não encontrada")

    def _run() -> dict:
        identity = authz.verify_identity(token, host=Config().host)
        result = rehydrate_mod.rehydrate_space(
            source_prod_space_id=body.source_prod_space_id, identity=identity, mode=body.mode,
            dev_space_id=body.dev_space_id, title=body.title,
            store=store, promotion_id=promotion_id)
        return {"space_id": result.space_id, "mode": result.mode, "title": result.title}

    return _engine_call("rehydrate_space", _run)


# --- F3: self-service access requests ---------------------------------------
#
# Request -> SoD-clean approval -> GOVERNED grant application (F2's sidecar->PR->apply_access.py
# path, never an app-direct w.grants/w.permissions call) -> audit. Mirrors the promotion
# endpoints' shape (persist via an injectable store; identity ALWAYS from the verified OBO token,
# never a header) but uses its OWN store/tables (`access_request_store`) — see that module's
# docstring for why it isn't folded into `promotion_store.audit_events`.


def _require_access_store():
    store = getattr(app.state, "access_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Persistência (Lakebase) indisponível.")
    return store


def _access_request_summary(r) -> dict:
    return {"id": r.id, "space_id": r.space_id, "space_title": r.space_title,
            "requester_email": r.requester_email, "note": r.note,
            "want_space_permission": r.want_space_permission,
            "space_permission_level": r.space_permission_level, "want_uc_select": r.want_uc_select,
            "state": r.state, "decided_by": r.decided_by, "decided_at": r.decided_at,
            "decision_note": r.decision_note, "pr_number": r.pr_number, "pr_url": r.pr_url,
            "created_at": r.created_at, "updated_at": r.updated_at}


def _access_audit_event(e) -> dict:
    return {"seq": e.seq, "event_type": e.event_type, "occurred_at": e.occurred_at,
            "actor_email": e.actor_email, "detail": e.detail}


class AccessRequestIn(BaseModel):
    space_id: str
    space_title: str | None = None
    note: str | None = None
    want_space_permission: bool = True
    space_permission_level: str = "CAN_RUN"
    want_uc_select: bool = False


@api.post("/access-requests")
def create_access_request(
    body: AccessRequestIn,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """A user who lacks access to a prod Space requests it. The requester identity is the caller's
    platform-VERIFIED identity (A2) — never a display header — so the SoD check on approval has a
    trustworthy value to compare against."""
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")
    store = _require_access_store()

    def _run() -> dict:
        identity = authz.verify_identity(token, host=Config().host)
        req = store.create_request(
            space_id=body.space_id, space_title=body.space_title,
            requester_email=identity.user_name, note=body.note,
            want_space_permission=body.want_space_permission,
            space_permission_level=body.space_permission_level, want_uc_select=body.want_uc_select)
        return {"request": _access_request_summary(req)}

    return _engine_call("create_access_request", _run)


@api.get("/access-requests")
def list_access_requests(
    scope: str = "mine",
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """`scope=mine` (default): the caller's own requests, any state — their status view.
    `scope=pending` (the approver queue): EVERY requester's `requested`-state requests — ROLE-GATED
    to a configured Steward/Admin (verified identity), same gate as promotions' `scope=all` (LB5).
    """
    store = _require_access_store()
    verified = _verified_email(x_forwarded_access_token, authorization)
    if scope == "mine":
        if verified is None:
            requests = store.list_requests()  # local/offline (no OBO token) — pre-A2 ergonomics
        else:
            requests = store.list_requests(requester_email=verified)
    elif scope == "pending":
        if not _is_admin(verified):
            raise HTTPException(status_code=403,
                                detail="Apenas Stewards/Admins veem a fila de aprovação.")
        requests = store.list_requests(state="requested")
    else:
        raise HTTPException(status_code=400, detail="scope inválido; use 'mine' ou 'pending'")
    return {"requests": [_access_request_summary(r) for r in requests]}


def _visible_access_request_or_403(store, request_id: str, verified_email: str | None):
    r = store.get_request(request_id)
    if r is None:
        raise HTTPException(status_code=404, detail="solicitação de acesso não encontrada")
    if verified_email is None or _is_admin(verified_email) or (
            r.requester_email.lower() == verified_email.lower()):
        return r
    raise HTTPException(status_code=403, detail="Sem permissão para esta solicitação.")


@api.get("/access-requests/{request_id}")
def get_access_request(
    request_id: str,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """A single request + its append-only audit trail. Owner-or-admin only (A2)."""
    store = _require_access_store()
    verified = _verified_email(x_forwarded_access_token, authorization)
    r = _visible_access_request_or_403(store, request_id, verified)
    return {"request": _access_request_summary(r),
            "audit": [_access_audit_event(e) for e in store.list_audit_events(request_id)]}


class AccessDecisionIn(BaseModel):
    approve: bool
    note: str | None = None


@api.post("/access-requests/{request_id}/decide")
def decide_access_request(
    request_id: str,
    body: AccessDecisionIn,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Approve or deny an access request. Approver authority is EXPLICIT: only a configured
    Steward/Admin (by VERIFIED identity, A2) may decide at all — a random authenticated user
    cannot self-serve their own approval by simply calling this endpoint. SoD is then enforced a
    SECOND time, server-side, inside the store itself (`AccessRequestStore.decide`): the approver's
    verified identity is compared against the REQUEST's OWN recorded `requester_email` (never a
    client-supplied value) and a match raises `SelfApprovalError` -> 403 (mirrors
    `prevent_self_review`; caught centrally in `_engine_call`).

    On approval, the grant is applied via the GOVERNED path ONLY (F2's sidecar -> bot PR ->
    `scripts/apply_access.py`, run by CI as the prod SP behind the Steward's deploy gate) — this
    endpoint never calls `w.grants`/`w.permissions`. A failure opening the PR after the decision is
    recorded is NOT silently swallowed: the request stays `approved` (not `applied`), an
    `apply_failed` audit event captures the reason, and the HTTP call itself surfaces the error
    (502/503 via `_engine_call`) so the Steward sees the grant did NOT land and can retry.
    """
    token = _user_token(x_forwarded_access_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="user token required (OBO)")
    store = _require_access_store()
    # Resolved + role-checked BEFORE _engine_call (not inside `_run`): _engine_call's broad
    # `except Exception` would otherwise catch this HTTPException and remap it to a generic 502,
    # hiding the real 403 from the caller.
    try:
        identity = authz.verify_identity(token, host=Config().host)
    except authz.AccessDenied:
        raise HTTPException(status_code=401, detail="Sessão expirada — recarregue para reautenticar.")
    if not _is_admin(identity.user_name):
        raise HTTPException(status_code=403,
                            detail="Apenas Stewards/Admins aprovam ou negam solicitações de acesso.")
    if store.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="solicitação de acesso não encontrada")

    def _run() -> dict:
        req = store.decide(request_id, approve=body.approve, approver_email=identity.user_name,
                           decision_note=body.note)
        if not body.approve:
            return {"request": _access_request_summary(req)}
        # Approved -> apply via the governed path (sidecar -> bot PR). A failure here must NOT look
        # like a silent success: record apply_failed and let the exception surface to the caller.
        try:
            applied = app_logic.apply_access_request(
                space_id=req.space_id, principal_email=req.requester_email,
                want_space_permission=req.want_space_permission,
                space_permission_level=req.space_permission_level,
                want_uc_select=req.want_uc_select, resource_title=req.space_title,
                approver_email=identity.user_name)
        except Exception as e:  # noqa: BLE001 — record the failure, then re-raise for _engine_call
            store.mark_apply_failed(request_id, actor_email=identity.user_name, reason=str(e))
            raise
        req = store.mark_applied(request_id, actor_email=identity.user_name,
                                 pr_number=applied["pr"]["number"], pr_url=applied["pr"]["url"])
        return {"request": _access_request_summary(req), "pr": applied["pr"]}

    return _engine_call("decide_access_request", _run)


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
            "created_at": p.created_at, "updated_at": p.updated_at,
            # F2: the declared AccessSpec persisted with the Promotion (independent of the review
            # snapshot, and of the sidecar's own PR/branch lifetime) — the Steward reviews this.
            "access_spec": p.access_spec}


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
def list_promotions(
    scope: str = "mine",
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """The caller's promotions (newest first), for recovery + the history view (LB3/LB5).
    `scope=mine` filters by the caller's platform-VERIFIED identity (A2 — NOT the display-only
    `x-forwarded-email` header; `requester_email` was itself recorded from that same display header
    at promotion time, per ADR-0005, so this still matches the honest case while no longer trusting
    the header as an authorization input for a DIFFERENT caller's request).
    `scope=all` (cross-user governance view) is ROLE-GATED server-side: only a configured
    Steward/Admin (by verified identity) gets it; anyone else is denied (403). Bot/SP owns the DB read."""
    store = _require_store()
    verified = _verified_email(x_forwarded_access_token, authorization)
    if scope == "mine":
        promotions = store.list_promotions(verified)  # None (no verified identity: local/no token) => all
    elif scope == "all":
        if not _is_admin(verified):
            raise HTTPException(status_code=403, detail="Apenas Stewards/Admins veem todas as promoções.")
        promotions = store.list_promotions(None)  # cross-user
    else:
        raise HTTPException(status_code=400, detail="scope inválido; use 'mine' ou 'all'")
    return {"promotions": [_promotion_summary(p) for p in promotions]}


def _visible_promotion_or_403(store, promotion_id: str, verified_email: str | None):
    """Fetch a promotion the caller may see: their own (requester_email == the caller's platform-
    VERIFIED identity) OR any if the caller is a Steward/Admin (also by verified identity). This is
    the entire per-user visibility boundary now (A2) — `verified_email` MUST come from
    `_verified_email` (the OBO-token-resolved identity), never from `x-forwarded-email` directly, or
    this collapses back into the exact "same untrusted header checked twice" no-op this replaces."""
    p = store.get_promotion(promotion_id)
    if p is None:
        raise HTTPException(status_code=404, detail="promoção não encontrada")
    # No verified identity == local/dev (no OBO token in play; the deployed proxy always supplies
    # one) -> allow, matching pre-A2 local-dev ergonomics. Otherwise the caller must own the
    # promotion (by verified identity) or be a verified admin.
    if verified_email is None or _is_admin(verified_email) or (
            p.requester_email and p.requester_email.lower() == verified_email.lower()):
        return p
    raise HTTPException(status_code=403, detail="Sem permissão para esta promoção.")


@api.get("/promotions/{promotion_id}")
def get_promotion(
    promotion_id: str,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """A single promotion + its LATEST stored Review Snapshot + PR ref — the recover-on-open payload
    (renders the stored review WITHOUT re-running the LLM reviewer). Live PR/CI/deploy status still
    comes from the GitHub poll (`/promote/{n}/status`), unchanged. Caller must own it (by verified
    identity) or be a verified admin (A2)."""
    store = _require_store()
    verified = _verified_email(x_forwarded_access_token, authorization)
    p = _visible_promotion_or_403(store, promotion_id, verified)
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
def get_promotion_audit(
    promotion_id: str,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """The ordered, append-only audit trail for a promotion (LB4) — refreshed by the SPA as the
    status poll reconciles, so the trail accrues live as the PR progresses. Owner-or-admin only,
    gated on the caller's VERIFIED identity (A2)."""
    store = _require_store()
    verified = _verified_email(x_forwarded_access_token, authorization)
    _visible_promotion_or_403(store, promotion_id, verified)
    return {"audit": [_audit_event(e) for e in store.list_audit_events(promotion_id)]}


# --- F4: admin console — live prod inventory + access-request queue + cross-Promotion audit -------
#
# One source of truth over prod (US-27..31): all three surfaces are READ-ONLY aggregation over data
# this app already owns (the Lakebase promotion + access-request stores) plus a live prod Genie read
# — F4 never mutates prod or a grant itself. Every endpoint here is gated the SAME way: resolve the
# caller's PLATFORM-VERIFIED identity (`_verified_email`, never `x-forwarded-email`) and require
# `_is_admin` on it, else 403 — server-side, so an admin-only surface can't be reached by simply
# hiding a UI affordance.


def _require_admin(x_forwarded_access_token: str | None, authorization: str | None) -> str:
    """Resolve + enforce the A2-hardened admin gate for an F4 endpoint. Returns the verified email
    (rarely needed by the caller, but handy for logging/consistency with the other gated endpoints).
    Raises 403 for anyone whose VERIFIED identity isn't a configured admin/Steward — including a
    caller with NO usable token at all (`_verified_email` -> None -> never admin), which is the
    correct fail-closed behavior for an admin surface (unlike the `scope=mine`-style endpoints,
    F4 has no legitimate "local/offline, no token" degraded mode to fall back to)."""
    verified = _verified_email(x_forwarded_access_token, authorization)
    if not _is_admin(verified):
        raise HTTPException(status_code=403, detail="Apenas Stewards/Admins acessam o console de administração.")
    return verified


@api.get("/admin/inventory")
def admin_inventory_endpoint(
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-27/US-30: every prod-deployed Genie Space (owner, declared access, current phase),
    reflecting LIVE prod state — `app_logic.list_prod_spaces` is called fresh on this request (never
    served from a cache), then joined against the Lakebase promotion index by `admin_inventory`. A
    live Space with no matching Promotion still appears (owner/access/phase all null → the UI
    renders "—"); a Promotion whose Space is no longer live is returned separately as
    `orphaned_promotions`, never silently dropped or crashed on."""
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_store()

    def _run() -> dict:
        return admin_inventory.load_inventory(app_logic.list_prod_spaces, store.list_promotions(None))

    return _engine_call("admin_inventory", _run)


@api.get("/admin/access-requests")
def admin_access_requests(
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-28: every access request in ANY state (pending/approved/denied/applied), newest first, in
    one place — reuses `AccessRequestStore.list_requests()` with no filters (already returns every
    requester/every state; F3's `scope=pending` narrows to `requested`-only for the approver's
    action queue, this is the admin's full-history view)."""
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_access_store()
    return {"requests": [_access_request_summary(r) for r in store.list_requests()]}


@api.get("/admin/audit")
def admin_audit(
    limit: int = 200,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-29: "who changed what, when" across EVERY Promotion (not just one), newest first. Unions
    each Promotion's own append-only audit trail (already ordered oldest->newest per-Promotion by
    `seq`) and re-sorts the combined set by `occurred_at` descending, carrying the acting identity
    (`actor_github_login` when GitHub-attributed, else the display-only `actor_app_email`) and which
    Promotion/resource the event belongs to, so an admin can trace an action back to its Promotion.
    `limit` bounds the response for a workspace with a long history (default 200, newest first —
    truncation drops the OLDEST events, never the most recent ones an admin is most likely to want).
    """
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_store()

    def _run() -> dict:
        # ONE joined query (store.list_recent_audit_events), newest-first + bounded — no per-Promotion
        # N+1 (F4 review should-fix). Rows are dicts already joined with resource_id/resource_title.
        rows = [{
            "seq": r["seq"], "event_type": r["event_type"], "occurred_at": r["occurred_at"],
            "actor_github_login": r["actor_github_login"], "actor_app_email": r["actor_app_email"],
            "github_event_at": r["github_event_at"], "detail": r["detail"],
            "promotion_id": r["promotion_id"], "resource_id": r["resource_id"],
            "resource_title": r["resource_title"],
        } for r in store.list_recent_audit_events(limit)]
        return {"audit": rows}

    return _engine_call("admin_audit", _run)


# --- F5 Phase 1: configurable roles (Lakebase-backed) + READ-ONLY GitHub drift detection ---------
#
# Roles CRUD is admin-gated the SAME way as F4 (`_require_admin`, on the VERIFIED identity). Drift
# is READ-ONLY (Phase 1 only — see genie_reviewer/github_drift.py's docstring for why Phase 2
# write-through is explicitly out of scope) and is surfaced from TWO places: a Settings screen
# (`GET /admin/roles`, `GET /admin/drift`) and, contextually, the promotion-review screen for the
# ASSIGNED Steward (`GET /promotions/{id}/drift`) — never only a passive panel nobody opens.


def _require_roles_store():
    store = _roles_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Persistência (Lakebase) indisponível.")
    return store


def _role_summary(r) -> dict:
    return {"id": r.id, "email": r.email, "role": r.role, "github_username": r.github_username,
            "created_at": r.created_at, "updated_at": r.updated_at}


@api.get("/admin/roles")
def list_roles(
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-32/US-33: every configured role assignment (Steward/Admin/approver) + its GitHub-username
    mapping. Admin-gated (A2-hardened verified identity, same as every other F4/F5 admin endpoint)."""
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_roles_store()
    return {"roles": [_role_summary(r) for r in store.list_all()],
            "bootstrap_env": {  # so the UI can show WHY a role is in effect when the store is empty
                "admins": sorted(_env_emails("APP_ADMINS")),
                "stewards": sorted(_env_emails("APP_STEWARDS", "APP_STEWARD")),
            }}


class RoleAssignIn(BaseModel):
    email: str
    role: str  # "steward" | "admin" | "approver"
    github_username: str | None = None


@api.post("/admin/roles")
def assign_role(
    body: RoleAssignIn,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-32: set who is Steward/Admin/approver, in-app, with NO redeploy. Idempotent upsert keyed
    on (email, role) — re-assigning the same role just refreshes the GitHub-username mapping."""
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_roles_store()
    try:
        assignment = store.assign(email=body.email, role=body.role,
                                  github_username=body.github_username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"role": _role_summary(assignment)}


class RoleRevokeIn(BaseModel):
    email: str
    role: str


@api.post("/admin/roles/revoke")
def revoke_role(
    body: RoleRevokeIn,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Remove a role assignment. Idempotent (a no-op if it didn't exist) — never a 404 for
    "already gone", mirroring the DELETE-semantics convention used elsewhere in this app."""
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_roles_store()
    try:
        store.revoke(email=body.email, role=body.role)
    except roles_store.LastAdminError as e:
        # Refuse a revoke that would lock everyone out of the admin console (incl. this CRUD).
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


def _run_drift_check() -> dict:
    """The actual GitHub read + comparison (US-34), shared by the Settings panel and the
    promotion-review contextual surface. Never raises: `github_drift.check_drift` degrades any
    GitHub read failure to an `unknown_*` finding internally; the one thing THIS wrapper guards
    against is the bot client itself failing to CONSTRUCT (e.g. the secret scope is unreachable),
    which is a distinct, even-earlier failure than a bad API response — surfaced the same way
    (an `unknown_environment` + `unknown_branch_protection` pair) so the caller never has to
    special-case "the reader couldn't even be built" vs "a GitHub call failed"."""
    store = _roles_store()
    stewards = sorted(_steward_emails())
    admins = sorted(_admin_emails())

    def github_username_for(email: str) -> str | None:
        return store.github_username_for(email) if store is not None else None

    try:
        reader = app_logic.github_app_factory()
    except Exception as e:  # noqa: BLE001 — the bot client itself is unreachable/misconfigured
        logger.warning("F5 drift check: could not build the GitHub bot client (%s)", e)
        unreachable = github_drift.DriftFinding(
            kind="unknown_environment", severity="unknown",
            message="Não foi possível conectar ao GitHub (bot) para verificar os gates de aprovação.",
            detail={"error": str(e)})
        return github_drift.summarize([unreachable])

    findings = github_drift.check_drift(
        stewards=stewards, admins=admins, github_username_for=github_username_for, reader=reader)
    return github_drift.summarize(findings)


@api.get("/admin/drift")
def admin_drift(
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-34: drift between the app's role config and GitHub's enforced gates (prod Environment
    required-reviewers + base branch protection), for the Settings screen. Admin-gated + READ-ONLY
    (Phase 1 — see github_drift.py)."""
    _require_admin(x_forwarded_access_token, authorization)
    return _engine_call("admin_drift", _run_drift_check)


@api.get("/promotions/{promotion_id}/drift")
def promotion_drift(
    promotion_id: str,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """US-35: the SAME drift check, surfaced CONTEXTUALLY on the promotion-review screen for the
    assigned Steward — not only a passive Settings panel nobody opens. Visible to whoever may see
    the promotion at all (owner or admin/Steward, the existing A2 visibility boundary) — the
    Steward specifically is who acts on it, but a Requester seeing "your Steward's GitHub gate
    looks misconfigured" is still useful, non-sensitive information (no secrets, just role-mapping
    divergence), so this reuses the existing visibility check rather than a stricter one."""
    store = _require_store()
    verified = _verified_email(x_forwarded_access_token, authorization)
    _visible_promotion_or_403(store, promotion_id, verified)
    return _engine_call("promotion_drift", _run_drift_check)


# --- G2: admin-configurable reviewer rules (Lakebase-backed, safe hardcoded fallback) -------------
#
# Admin-gated the SAME way as F4/F5 (`_require_admin` on the VERIFIED identity). `review()`/
# `promote()` above already read `_rules_store()` and thread the override rows into the engine
# (`app_logic.review_space`/`request_promotion` -> `rules_config.effective_rules`/`eval01_config`);
# these three endpoints are the CRUD the Settings screen's "Regras" section calls.


def _rules_store():
    return getattr(app.state, "rules_store", None)


def _require_rules_store():
    store = _rules_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Persistência (Lakebase) indisponível.")
    return store


def _override_summary(o) -> dict:
    return {"rule_id": o.rule_id, "is_custom": o.is_custom, "enabled": o.enabled,
            "severity": o.severity, "params": o.params, "content": o.content,
            "citation": o.citation, "updated_by": o.updated_by, "updated_at": o.updated_at}


@api.get("/admin/rules")
def list_rules(
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """The EFFECTIVE rule set (hardcoded `handbook_rules.RULES` merged with any admin overrides —
    the exact set `review()` grounds the LLM on) alongside the raw override rows, so the Settings
    screen can render "9 hardcoded + N custom" with each hardcoded rule's override state (or none)."""
    _require_admin(x_forwarded_access_token, authorization)
    store = _require_rules_store()
    overrides = store.list_all_dicts()
    return {"effective": rules_config.effective_rules(overrides),
            "overrides": [_override_summary(o) for o in store.list_all()],
            "hardcoded": handbook_rules.RULES}


class RuleUpsertIn(BaseModel):
    rule_id: str
    is_custom: bool = False
    enabled: bool = True
    severity: str | None = None
    params: dict | None = None
    content: str | None = None
    citation: str | None = None


@api.post("/admin/rules")
def upsert_rule(
    body: RuleUpsertIn,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Disable/re-enable a hardcoded rule, override its severity/params, or create/update a custom
    rule — takes effect on the NEXT review, no redeploy. A plain override (`is_custom=False`) must
    name one of the 9 hardcoded `rule_id`s (checked here, against `handbook_rules.RULES` — the
    store itself is rule_id-agnostic so it can also hold genuinely new custom ids); a custom rule
    must NOT collide with a hardcoded id. Audited (`rule_created`/`rule_updated`) under the caller's
    VERIFIED identity."""
    verified = _require_admin(x_forwarded_access_token, authorization)
    store = _require_rules_store()
    known_ids = {r["rule_id"] for r in handbook_rules.RULES}
    if body.is_custom and body.rule_id in known_ids:
        raise HTTPException(status_code=400,
                            detail=f"rule_id {body.rule_id!r} já é uma regra do handbook — use override, não custom.")
    if not body.is_custom and body.rule_id not in known_ids:
        raise HTTPException(status_code=400,
                            detail=f"rule_id {body.rule_id!r} não é uma regra conhecida do handbook.")
    try:
        override = store.upsert(
            rule_id=body.rule_id, actor_email=verified, is_custom=body.is_custom,
            enabled=body.enabled, severity=body.severity, params=body.params,
            content=body.content, citation=body.citation)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"rule": _override_summary(override)}


class RuleResetIn(BaseModel):
    rule_id: str


@api.post("/admin/rules/reset")
def reset_rule(
    body: RuleResetIn,
    x_forwarded_access_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    """Reset a hardcoded rule back to its default (removes the override) or delete a custom rule
    entirely. Idempotent — a no-op (200, not 404) if there was nothing to reset. Audited
    (`rule_reset`/`rule_deleted`) under the caller's VERIFIED identity."""
    verified = _require_admin(x_forwarded_access_token, authorization)
    store = _require_rules_store()
    store.reset(body.rule_id, actor_email=verified)
    return {"ok": True}


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

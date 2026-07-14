"""Backend logic for the Acme promotion App (S8-S11) — testable, no Streamlit.

Wires the live Genie API + the proven engine (pre_render allowlist, Genie Reviewer,
eval gate) into the operations the UI needs:
  list_spaces(profile)                 -> the author's Genie Spaces
  review_space(space_id, ...)          -> the full promotion review (the hero flow):
      export -> pre-render dev_->prod_ -> deterministic allowlist (ENV-01) + grant
      check (GRANT-01) -> agent review (EVAL-01 deterministic) -> eval gate -> findings
      + gate + timeline
  GO-LIVE (not yet wired — need the S3 GitHub repo): request_promotion() opens the PR
  and the Steward approval releases the gate; create_space() calls genie create-space.

Uses the Databricks SDK (WorkspaceClient). Locally a CLI profile selects the
workspace; on the deployed Databricks App ``profile`` is None and the app's service
principal authenticates automatically. The live calls take an injectable ``client``,
so SDK-response parsing is unit-tested with a fake — no network, runs offline.
"""
from __future__ import annotations

import json
import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.service.catalog import SecurableType
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("genie_reviewer", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import base64  # noqa: E402

import access_spec  # noqa: E402  (F2 — the declared-access model; Space perms + UC grants)
import authz  # noqa: E402  (A2 — verified identity + the live fail-closed access guard)
import eval_gate  # noqa: E402
import grant_check  # noqa: E402
import handbook_rules  # noqa: E402
import pre_render  # noqa: E402
import review_core  # noqa: E402
import rules_config  # noqa: E402  (G2 — admin-configurable rules; safe fallback to handbook_rules.RULES)
from github_app import GitHubApp  # noqa: E402
from promotion_comment import PROMOTION_COMMENT_MARKER, render_promotion_comment  # noqa: E402

LLM = os.environ.get("APP_LLM_ENDPOINT", "databricks-claude-opus-4-8")
DOMAIN = os.environ.get("APP_DOMAIN", "recebiveis")

# GitHub PR integration (GH2). The committed source the promotion PR edits is the DEV-shaped
# serialized_space; CI (scripts/render.sh) rebinds dev_->prod_ at validate/deploy — so the PR
# touches src/, never build/. The bot's creds live in a Databricks secret scope the app SP reads.
GH_REPO = os.environ.get("APP_GH_REPO", "malcolndandaro/genie-promote-cicd")
GH_SECRET_SCOPE = os.environ.get("APP_GH_SECRET_SCOPE", "genie_promote")
# PER-SPACE promotion: each Genie space promotes on its OWN branch + committed file, so concurrent
# promotions of different spaces never collide on a shared PR. A space's SLUG keys the branch, the
# committed artifact path, AND the generated prod genie_spaces resource (scripts/render.sh). Friendly/
# legacy slugs are pinned via APP_SPACE_SLUGS (JSON {space_id: slug}) — e.g. the canonical demo space
# keeps "receivables" so its existing file/PR/resource are preserved; otherwise the slug is derived
# from the id (prefixed so it's a valid branch + DABs resource key).
GH_PROMOTION_BRANCH_PREFIX = os.environ.get("APP_GH_PROMOTION_BRANCH_PREFIX", "promote")
# F3: a distinct branch prefix for access-request grant PRs — separate from a space's own
# promote/<slug> branch so an in-flight promotion and an in-flight access request on the SAME space
# never collide on one branch/PR (each is independently reviewable + mergeable).
GH_ACCESS_BRANCH_PREFIX = os.environ.get("APP_GH_ACCESS_BRANCH_PREFIX", "access")
GH_GENIE_SRC_DIR = "src/genie"
try:
    _SPACE_SLUGS = json.loads(os.environ.get("APP_SPACE_SLUGS") or "{}")
except (ValueError, TypeError):
    _SPACE_SLUGS = {}


def space_slug(space_id: str) -> str:
    """Stable, branch/path/identifier-safe slug for a space. A pinned slug (APP_SPACE_SLUGS) wins;
    otherwise derive from the id (alnum/underscore only, guaranteed to start with a letter)."""
    pinned = _SPACE_SLUGS.get(space_id)
    if pinned:
        return pinned
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in space_id)
    return safe if safe[:1].isalpha() else f"s_{safe}"


def branch_for(slug: str) -> str:
    return f"{GH_PROMOTION_BRANCH_PREFIX}/{slug}"


def access_branch_for(slug: str) -> str:
    """F3: the branch an approved access request's sidecar update PR opens on — distinct from
    ``branch_for`` (a space's own promotion branch) so the two flows never collide."""
    return f"{GH_ACCESS_BRANCH_PREFIX}/{slug}"


def src_path_for(slug: str) -> str:
    return f"{GH_GENIE_SRC_DIR}/{slug}.serialized_space.json"


def access_path_for(slug: str) -> str:
    """The per-space AccessSpec sidecar path (F2) — mirrors the `.title` sidecar pattern: committed
    next to the artifact so CI's render.sh/check_grants.py/apply_access.py find it by convention,
    with no shared manifest (concurrent per-space PRs never conflict)."""
    return f"{GH_GENIE_SRC_DIR}/{slug}.access.json"


def mapping_path_for(slug: str) -> str:
    """G7: the per-space table de-para sidecar path — mirrors `.title`/`.access.json`: committed
    next to the artifact so CI's render.sh finds it by convention (`pre_render.py apply-mapping`,
    applied AFTER the dev_->prod_ rebind, BEFORE the strict allowlist check)."""
    return f"{GH_GENIE_SRC_DIR}/{slug}.mapping.json"

# The UI-facing fields of a review (drop the large prod_serialized payload) — shared with the API.
REVIEW_FIELDS = ("findings", "gate", "eval", "timeline", "allowlist_violations", "consumer_group",
                 "access_spec")

# --- Cross-workspace client factory (ADR-0006 / A1) --------------------------------------------
#
# The app now runs in PROD (a durable control plane — ADR-0006 supersedes ADR-0002's "app is the
# dev front door" framing) and reaches INTO dev for the two Genie-API operations that must run
# there: reading the authored Space at promotion time, and (in a later slice, F1) writing a
# rebound Space back to dev. Every other operation is prod-local.
#
# Config-driven (ADR-0004): the dev workspace host + the dev-reader/writer SP's secret-scope
# location are env vars, never hardcoded. The SP's OWN credentials are never bundle variables or
# code — they live in a Databricks secret scope, read at call time via the app's own identity
# (whichever prod-local client is already authenticated).
#
# NOTE (A1 scope): the dev-reader/writer SP is *provisioned + bootstrapped in A2*
# (scripts/provision_dev_sp.sh, per SP2's self-heal finding — dev's ~7-day wipe means the SP's
# dev-side grants must be re-assertable, not just its account-level identity). A1 only builds the
# factory SHAPE + config wiring: the dev-remote-SP path constructs lazily (no network at import or
# construction time) and is unit-tested with an injected/faked client; it is not exercised live
# until A2 lands the SP + its secret.
APP_DEV_HOST = os.environ.get("APP_DEV_HOST")
APP_DEV_SP_SECRET_SCOPE = os.environ.get("APP_DEV_SP_SECRET_SCOPE", "genie_promote")
DEV_SP_CLIENT_ID_KEY = "dev_sp_client_id"
DEV_SP_CLIENT_SECRET_KEY = "dev_sp_client_secret"


def _client(profile: str | None = None, *, user_token: str | None = None,
            scope: str = "prod") -> WorkspaceClient:
    """Build a Databricks SDK WorkspaceClient for one of four auth contexts. This is the ONE place
    target/credential selection lives (US-4) — every call site goes through here.

    ``scope="prod"`` (default) — the workspace the app itself runs in (prod, post-ADR-0006):
    - ``user_token`` (deployed app, OBO): act *as the logged-in user* via their forwarded
      OAuth token (the ``x-forwarded-access-token`` header). Host comes from the
      auto-injected env. Used for user-facing reads so the app respects the user's own
      data access (and the audit trail is per-user), never the app's broader rights.
    - ``profile`` (local dev): select a workspace from ``~/.databrickscfg``.
    - neither (deployed app, shared ops): the app's service principal authenticates
      automatically via injected env — used for the LLM reviewer + the prod grant preview.

    ``scope="dev-sp"`` — the DEV workspace, reached via the standing dev-reader/writer service
    principal (never OBO: once the app is prod-hosted there is no forwarded dev token — the
    cross-workspace identity is load-bearing, see ADR-0006/A2). Host = ``APP_DEV_HOST``;
    credentials are read from the ``APP_DEV_SP_SECRET_SCOPE`` secret scope via the CALLING
    client's own identity (an explicit ``secret_client`` may be injected for tests/bootstrapping
    without a live prod client). Raises ``RuntimeError`` if ``APP_DEV_HOST`` isn't configured —
    fail loud rather than silently falling back to prod.

    Construction is lazy (no network call in any branch), so this is safe to build/test offline.
    """
    if scope == "dev-sp":
        return _dev_sp_client()
    if user_token:
        # auth_type="pat" so the SDK uses ONLY the forwarded user token. Without it the app
        # SP's auto-injected OAuth env (DATABRICKS_CLIENT_ID/SECRET) collides with the token
        # -> "more than one authorization method configured: oauth and pat".
        return WorkspaceClient(host=Config().host, token=user_token, auth_type="pat")
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def _dev_sp_client(*, secret_client: WorkspaceClient | None = None) -> WorkspaceClient:
    """The dev-remote-SP client (scope="dev-sp"): authenticates as the dev-reader/writer SP against
    the DEV workspace host. Construction is lazy — the secret read + WorkspaceClient() call only
    happen when a caller actually invokes this (not at import time), so wiring this in is safe
    before the SP exists (A1) and before it's ever called live (A2 provisions the SP + secret).

    ``secret_client`` is the prod-local client used to READ the dev SP's secret (the app's own
    identity holds the READ grant on the scope, mirroring ``_github_app``'s pattern); defaults to
    the app SP / local profile. Injectable so this is unit-testable with a fake — no network.
    """
    if not APP_DEV_HOST:
        raise RuntimeError(
            "APP_DEV_HOST is not configured — the cross-workspace (dev-sp) client requires the "
            "dev workspace host (ADR-0004: config-driven, never hardcoded).")
    w = secret_client or _client()
    client_id = _secret(w, APP_DEV_SP_SECRET_SCOPE, DEV_SP_CLIENT_ID_KEY)
    client_secret = _secret(w, APP_DEV_SP_SECRET_SCOPE, DEV_SP_CLIENT_SECRET_KEY)
    return WorkspaceClient(host=APP_DEV_HOST, client_id=client_id, client_secret=client_secret)


def list_spaces(profile: str | None = None, *, client: WorkspaceClient | None = None,
                user_token: str | None = None) -> list[dict]:
    """The author's Genie Spaces the VERIFIED identity may access (id + title).

    Cross-workspace rewiring (A2 / ADR-0006 half-migration fix): once the app is prod-hosted, OBO
    cannot reach the dev workspace — there is no forwarded dev token — so this reaches dev via the
    standing dev-reader/writer SP (``scope="dev-sp"``), never the user's own token. The dev SP can
    list EVERY dev Space by platform necessity (Genie has no per-Space delegation), which is exactly
    the confused-deputy risk A2 exists to close: this function does NOT return the SP's raw view.
    Instead it (1) verifies the caller's identity from their OBO token
    (``authz.verify_identity``) and (2) filters the SP's list down to Spaces
    ``authz.assert_can_access`` confirms that VERIFIED identity may use — a live, per-Space, never-
    cached ACL check (fail-closed: a Space is dropped from the list on any check error, never
    included on uncertainty).

    ``client`` (injected) bypasses ALL of the above and is used as-is — the existing test/local-
    override path (mirrors every other function in this module): callers that inject a fake/real
    client are asserting they already hold the right transport + have done their own gating.
    A bare local ``profile`` with no ``user_token`` (offline/local-dev convenience — e.g. a
    developer running the engine against their own CLI profile with no app/OBO in the loop) also
    skips the guard: there is no separate "caller" to check against the profile's own access, so the
    profile's own Genie permissions ARE the access boundary, same as running the CLI directly.
    """
    if client is not None:
        w = client
    elif user_token:
        # Verify WHO the OBO token belongs to against the app's OWN (prod) host — the token is
        # prod-minted and cannot authenticate to dev (ADR-0006 Decision 2). Only the transport
        # (scope="dev-sp") and the ACL read (assert_can_access) run against dev; the identity is
        # prod-issued. (Same host as engine_api._verified_email.)
        identity = authz.verify_identity(user_token, host=Config().host)
        dev = _client(scope="dev-sp")
        resp = dev.genie.list_spaces()
        accessible = []
        for s in (resp.spaces or []):
            try:
                authz.assert_can_access(identity, s.space_id, transport=dev)
            except authz.AccessDenied:
                continue
            accessible.append({"space_id": s.space_id, "title": s.title or "(sem título)"})
        return accessible
    else:
        w = _client(profile)
    resp = w.genie.list_spaces()
    return [{"space_id": s.space_id, "title": s.title or "(sem título)"}
            for s in (resp.spaces or [])]


def list_prod_spaces(profile: str | None = None, *, client: WorkspaceClient | None = None) -> list[dict]:
    """F4: EVERY Genie Space currently deployed in PROD (id + title) — the raw feed the admin
    inventory (`admin_inventory.load_inventory`) joins against the promotion index.

    Deliberately NOT identity-filtered like `list_spaces`: this is an ADMIN-ONLY read (gated at the
    API layer on the A2-hardened `_is_admin` check, never exposed to a non-admin caller), and its
    whole purpose is a cross-user inventory of what's live in prod — filtering it down to "what this
    admin could open themselves" would defeat that. Because the app itself now runs IN prod
    (ADR-0006), this is a plain same-workspace call as the app's own prod-local identity (the SP in
    the deployed app, or a `profile` locally) — no OBO, no dev-sp cross-workspace hop, no
    `assert_can_access` guard (that guard exists specifically for the confused-deputy risk of the
    STANDING DEV SP's broad reach; there is no equivalent standing broad-reach credential on the
    prod side here — the app SP's own prod Genie access IS the intended admin-inventory boundary,
    same as every other prod-local admin read in this app).

    Called FRESH on every invocation (no caching anywhere in this call chain) so the inventory
    reflects live prod state, per the F4 acceptance criteria — a caller must not wrap this in any
    memoization.
    """
    w = client or _client(profile)
    resp = w.genie.list_spaces()
    return [{"space_id": s.space_id, "title": s.title or "(sem título)"} for s in (resp.spaces or [])]


def list_principals(query: str = "", *, profile: str | None = None, client: WorkspaceClient | None = None,
                    user_token: str | None = None, kind: str = "all", limit: int = 25) -> list[dict]:
    """G1: users + groups of the workspace directory (SCIM ``w.users.list``/``w.groups.list``) —
    the ONE source every principal picker in the app (F2 access declarations, F3 access requests, F5
    role assignment) draws from, so a business user never has to type a raw email/username. Free
    text is a SEARCH QUERY only, filtered server-side via a SCIM ``co`` (contains) clause on
    userName/displayName; a blank query returns the first ``limit`` of each kind, so a picker opens
    already prefilled before the user types anything.

    Directory listing is a same-workspace-prod read (the app's own workspace, post-ADR-0006) and,
    unlike Genie Space listing, is not itself an access boundary — there's no dev-sp cross-workspace
    hop or ``assert_can_access`` guard here. The read runs as the APP SP by default: the
    Apps-forwarded OBO token is DOWNSCOPED to the app's ``user_api_scopes`` (today only
    ``dashboards.genie``), so SCIM calls 403 on it — verified live. ``user_token`` remains accepted
    for local/dev callers whose token has full scopes; the deployed endpoint gates the CALLER on
    their OBO token but passes no token here. A bare ``profile``/injected ``client`` is the existing
    local/offline/test convention.

    G9 (Additional scope, found live 2026-07-13): every GROUP carries a ``uc_grantable`` flag — UC
    grants reject workspace-LOCAL groups (the built-in ``users``/``admins``, or any workspace-scoped
    custom group), so the UC-principals half of ``AccessSpecForm`` must not offer one
    (``grant_check.is_uc_grantable_group``, keyed off the group's SCIM ``meta.resourceType``: account
    groups report ``"Group"``, workspace-local groups report ``"WorkspaceGroup"``). The Space
    -permissions half keeps listing every group unfiltered — workspace ACLs accept them fine. Every
    USER is always ``True`` (an individual is always account-level).
    """
    w = client or _client(profile, user_token=user_token)
    q = query.strip()
    principals: list[dict] = []

    if kind in ("all", "user"):
        user_filter = f'userName co "{q}" or displayName co "{q}"' if q else None
        count = 0
        for u in w.users.list(filter=user_filter):
            principals.append({"type": "user", "id": u.id, "display": u.display_name or u.user_name or u.id,
                               "email": u.user_name, "uc_grantable": True})
            count += 1
            if count >= limit:
                break

    if kind in ("all", "group"):
        group_filter = f'displayName co "{q}"' if q else None
        count = 0
        for g in w.groups.list(filter=group_filter):
            resource_type = getattr(getattr(g, "meta", None), "resource_type", None)
            display = g.display_name or g.id
            principals.append({"type": "group", "id": g.id, "display": display, "email": None,
                               "uc_grantable": grant_check.is_uc_grantable_group(resource_type, display)})
            count += 1
            if count >= limit:
                break

    return principals


def export_serialized(space_id: str, profile: str | None = None, client: WorkspaceClient | None = None,
                      *, user_token: str | None = None) -> dict:
    """Export a Space's ``serialized_space``. Cross-workspace rewiring (A2): with a ``user_token``
    and no injected ``client``, this reaches dev via the dev-reader/writer SP (OBO can't span
    workspaces once prod-hosted) but gates FIRST with ``authz.assert_can_access`` on the caller's
    VERIFIED identity — denying (fail-closed) before ever touching the SP's broad dev reach. An
    injected ``client`` (tests/local overrides) bypasses the guard, same convention as
    ``list_spaces``; a bare ``profile`` with no token is the offline/local-dev path (the profile's
    own Genie permission IS the access boundary, same as the CLI)."""
    if client is not None:
        w = client
    elif user_token:
        # Verify WHO the OBO token belongs to against the app's OWN (prod) host — the token is
        # prod-minted and cannot authenticate to dev (ADR-0006 Decision 2). Only the transport
        # (scope="dev-sp") and the ACL read (assert_can_access) run against dev; the identity is
        # prod-issued. (Same host as engine_api._verified_email.)
        identity = authz.verify_identity(user_token, host=Config().host)
        w = _client(scope="dev-sp")
        authz.assert_can_access(identity, space_id, transport=w)  # raises AccessDenied -> deny first
    else:
        w = _client(profile)
    space = w.genie.get_space(space_id, include_serialized_space=True)
    ss = space.serialized_space
    return json.loads(ss) if isinstance(ss, str) else (ss or {})


def preview_promotion(space_id: str, *, user_token: str, domain: str = DOMAIN,
                      dev_client: WorkspaceClient | None = None) -> dict:
    """G7: a READ-ONLY preview of a promotion's table de-para, called BEFORE ``request_promotion``
    so the confirm step can render an editable prod Space name + a table de-para for the caller to
    review/override BEFORE committing to a mapping at all. Persists nothing.

    Mirrors ``rehydrate.preview_rehydrate``'s shape but in the OPPOSITE direction: it exports the
    DEV Space (never prod, and never via ``preview_rehydrate`` — that one reads PROD as the app SP)
    through the SAME dev-sp transport + verified-identity ``assert_can_access`` guard
    ``export_serialized`` already uses for the review's own export (the ONE blast site a preview
    touches), and rebinds dev_->prod_ (the promotion direction) rather than prod_->dev_.

    Returns ``{title, tables: [{source, default_target}]}`` — every DISTINCT 3-part ref the dev
    Space references (via ``pre_render.find_refs``, including refs buried inside example/benchmark
    SQL), each with the plain dev_->prod_ default target ``request_promotion``'s CI render would
    use if the caller overrides nothing."""
    if dev_client is not None:
        dev = dev_client
    else:
        identity = authz.verify_identity(user_token, host=Config().host)
        dev = _client(scope="dev-sp")
        authz.assert_can_access(identity, space_id, transport=dev)  # raises AccessDenied -> deny first
    space = dev.genie.get_space(space_id, include_serialized_space=True)
    ss = space.serialized_space
    raw = ss if isinstance(ss, str) else json.dumps(ss or {}, ensure_ascii=False)

    tables = [{"source": ref, "default_target": pre_render.rebind(ref, "dev", "prod", domain)}
             for ref in pre_render.find_refs(raw)]
    return {"title": getattr(space, "title", None), "tables": tables}


def _claude(system: str, user: str, profile: str, client: WorkspaceClient | None = None) -> str:
    w = client or _client(profile)
    resp = w.serving_endpoints.query(
        name=LLM,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=system),
            ChatMessage(role=ChatMessageRole.USER, content=user),
        ],
        max_tokens=3000,
    )
    choices = resp.choices or []
    return choices[0].message.content if choices and choices[0].message else ""


def build_timeline(checks_ok: bool, gate: dict, eval_res: dict, approved: bool, deployed: bool) -> list[dict]:
    """The promotion status timeline the UI renders (S9)."""
    def st(done, fail=False, running=False):
        return "fail" if fail else "pass" if done else "running" if running else "pending"
    review_fail = gate.get("conclusion") == "failure"
    return [
        {"key": "checks", "label": "Checagens determinísticas (pré-render + allowlist)", "status": st(checks_ok, fail=not checks_ok)},
        {"key": "review", "label": "Revisão do agente (Genie Reviewer)", "status": st(not review_fail, fail=review_fail)},
        {"key": "eval", "label": f"Eval-run ({eval_res.get('status', 'n/a')})",
         "status": "fail" if eval_res.get("status") == "block" else "pass"},
        {"key": "approval", "label": "Aprovação do Steward", "status": st(approved, running=not approved)},
        {"key": "deploy", "label": "Deploy em produção (service principal)", "status": st(deployed)},
    ]


def _priv_name(p) -> str | None:
    """Normalize a privilege to its string form. The SDK returns a ``Privilege``
    enum (``.value`` == 'SELECT'); a plain string passes through unchanged."""
    return getattr(p, "value", p)


def _effective_grants(profile: str, full_name: str, client: WorkspaceClient | None = None) -> list[dict]:
    """Effective UC privileges on a table (includes schema/catalog-inherited grants)."""
    w = client or _client(profile)
    # SDK 0.111 takes securable_type as a plain string ("TABLE"); the enum's str repr
    # ("SecurableType.TABLE") is rejected by the API, so pass .value.
    resp = w.grants.get_effective(securable_type=SecurableType.TABLE.value, full_name=full_name)
    out = []
    for pa in (resp.privilege_assignments or []):
        privs = [_priv_name(p.privilege) for p in (pa.privileges or [])]
        out.append({"principal": pa.principal, "privileges": [x for x in privs if x]})
    return out


def _pii_access_findings(space: dict, spec: "access_spec.AccessSpec") -> list[dict]:
    """PII-01/02 interplay (F2 acceptance criteria): a handbook-grounded, deterministic note when
    the Requester declares broad access (UC SELECT and/or Space CAN_RUN/CAN_VIEW) to ANY group on a
    Space whose columns include an unmasked bank-secrecy/PII signal (CPF, titular/account-holder
    identity, PAN/card number — the same signal PII-01 itself looks for, kept in sync with
    handbook_rules.RULES so this doesn't drift into a second source of truth). This does NOT
    replace the LLM's own PII-01 judgment (a real exposure is still a BLOCKER there); it's a
    SUGGESTION surfaced specifically because F2 adds new principals who wouldn't otherwise have
    seen this Space's data — the Steward should double-check masking before approving THIS access
    grant, not just the Space's existing grants."""
    if spec.is_empty():
        return []
    pii_terms = handbook_rules.PII_SIGNAL_TERMS  # shared single source of truth (no drift)
    flagged_cols: list[str] = []
    for t in (space.get("data_sources") or {}).get("tables", []) or []:
        for c in (t.get("column_configs") or []):
            name = (c.get("column_name") or "").lower()
            if any(term in name for term in pii_terms):
                flagged_cols.append(f"{t.get('identifier', '')}.{c.get('column_name')}")
    if not flagged_cols:
        return []
    principals = ", ".join(spec.all_principals())
    return [{
        "severity": "SUGGESTION", "rule_id": "PII-01",
        "citation": "Genie Promotion Handbook › PII › PII-01",
        "message": (f"acesso declarado para {principals} inclui colunas com sinal de "
                    f"PII/sigilo bancário ({', '.join(flagged_cols)}) — confirme máscara/row filter "
                    f"antes de aprovar, para não ampliar exposição a um grupo não autorizado."),
        "suggestion": "Revisar máscaras de coluna UC nessas tabelas antes de aprovar o AccessSpec.",
    }]


def _non_grantable_declared_groups(gw: WorkspaceClient, spec: "access_spec.AccessSpec") -> list[str]:
    """G9: which of the AccessSpec's declared GROUP principals apply_access could never actually
    grant (workspace-local groups — UC grants reject those; `grant_check.is_uc_grantable_group`).
    Individual users are never checked — always account-level. Best-effort per group: a lookup
    failure falls back to the pure predicate's own unverifiable-group heuristic rather than
    aborting the whole (already best-effort) GRANT-01 preview."""
    out = []
    for name in spec.declared_groups():
        try:
            found = list(gw.groups.list(filter=f'displayName eq "{name}"'))
            resource_type = getattr(getattr(found[0], "meta", None), "resource_type", None) if found else None
        except Exception:  # noqa: BLE001
            resource_type = None
        if not grant_check.is_uc_grantable_group(resource_type, name):
            out.append(name)
    return out


def review_space(space_id: str, profile: str | None = None, to_env: str = "prod", domain: str = DOMAIN,
                 consumer_group: str | None = None, grant_profile: str | None = None,
                 user_token: str | None = None,
                 access_spec_: "access_spec.AccessSpec | None" = None,
                 rule_overrides: list[dict] | None = None) -> dict:
    """The hero flow: export -> pre-render -> allowlist (ENV-01) + grant check (GRANT-01)
    -> agent review (EVAL-01 deterministic) -> eval gate. Deterministic findings own their
    rule_id (no double-count). Returns findings/gate/eval/allowlist_violations/timeline/prod_serialized.

    ``rule_overrides`` (G2, optional) is the raw admin-configured override rows from
    `app/rules_store.py` (`RulesStore.list_all_dicts()`), or `None` when no store is bound —
    `rules_config.effective_rules`/`eval01_config` degrade to `handbook_rules.RULES`'s hardcoded
    defaults in that case, so a caller with no store behaves EXACTLY as before this rule ever
    existed.

    The in-app GRANT-01 preview only runs when an explicit, reachable prod target is
    configured (grant_profile arg or APP_PROD_PROFILE locally) — the prod catalog is
    workspace-isolated, so the dev-hosted app can't see it. On the deployed app it's skipped;
    the AUTHORITATIVE GRANT-01 runs in CI (pr-checks) as the prod SP (scripts/check_grants.py).

    ``access_spec_`` (F2, optional — named with a trailing underscore to avoid shadowing the
    ``access_spec`` module import) is the Requester's declared `AccessSpec` for this promotion.
    When given, GRANT-01's preview checks EVERY principal it declares (union with
    ``consumer_group``, never a replacement — F2 never weakens the base check), and a PII-01
    interplay SUGGESTION is added if the declared access reaches a column with a PII/bank-secrecy
    signal. The AccessSpec itself is echoed back on the result (``access_spec``) so the Steward
    reviews the SAME declaration that will be committed to the promotion sidecar.

    Cross-workspace rewiring (A2 / ADR-0006 half-migration fix): the export step is delegated to
    ``export_serialized`` UNCLIENTED (no ``client=`` injected here) so it does its own identity
    verification + ``assert_can_access`` gating + dev-sp transport selection when ``user_token`` is
    set — this function no longer builds an OBO client for the export itself (OBO cannot reach dev
    once the app is prod-hosted). Locally (``profile``, no token) the export still runs against the
    given profile, unchanged.
    """
    consumer_group = consumer_group or os.environ.get("APP_CONSUMER_GROUP", "account users")
    grant_profile = grant_profile or os.environ.get("APP_PROD_PROFILE")  # None -> skip preview
    spec = access_spec_ or access_spec.AccessSpec()
    # export_serialized resolves its OWN transport: dev-sp + assert_can_access(verified identity)
    # when user_token is set (the only way to reach dev post-ADR-0006), else the local profile.
    dev_serialized = export_serialized(space_id, profile, user_token=user_token)
    rendered = pre_render.rebind(json.dumps(dev_serialized, ensure_ascii=False), "dev", to_env, domain)
    prod_space = json.loads(rendered)
    violations = pre_render.find_violations(rendered, to_env, domain)  # deterministic ENV allowlist

    # Deterministic findings: ENV-01 (allowlist) + GRANT-01 (effective-grant check). These own
    # their rule_id via finalize_findings, so the LLM can't soften them and they aren't duplicated.
    deterministic: list[dict] = [
        {"severity": "BLOCKER", "rule_id": "ENV-01",
         "citation": "Genie Promotion Handbook › Catalog-per-Env › ENV-01",
         "message": f"referência a catálogo fora de {to_env}_{domain}: {v}",
         "suggestion": "Remover/pré-renderizar a referência de outro ambiente."}
        for v in violations
    ]
    # GRANT-01 preview (G9): consumer_group stays the BASELINE class (missing SELECT -> BLOCKER,
    # unchanged); every principal the AccessSpec declares (F2) is checked SEPARATELY as the
    # DECLARED class (missing SELECT -> non-blocking SUGGESTION — apply_access grants these at
    # deploy, so blocking pre-merge on it would be contradictory).
    if grant_profile:  # only preview when a reachable prod target is configured (else CI owns it)
        try:
            gw = _client(grant_profile)  # built here so a bad/unreachable prod profile can't break the review
            non_grantable = _non_grantable_declared_groups(gw, spec)
            deterministic += grant_check.check_grants(
                prod_space, consumer_group, lambda fq: _effective_grants(grant_profile, fq, client=gw),
                declared_principals=spec.all_principals(), non_grantable_principals=non_grantable)
        except Exception:  # noqa: BLE001 — grant check is best-effort; never break the review
            pass
    deterministic += _pii_access_findings(prod_space, spec)

    # G2: the EFFECTIVE rule set (hardcoded RULES + admin overrides/custom rules) grounds the
    # prompt; EVAL-01's threshold/severity backstop is resolved the same way (finalize_findings
    # owns that check deterministically — see rules_config's docstring for why).
    effective = rules_config.effective_rules(rule_overrides)
    min_bench, eval01_severity = rules_config.eval01_config(rule_overrides)
    ctx = review_core.build_space_context(prod_space)
    system, user = review_core.build_review_prompt(ctx, effective, deterministic)
    # LLM reviewer = shared operation -> app SP / local profile (a serving scope isn't part of
    # OBO; the reviewer agent doesn't read the user's data, and it never runs against dev).
    sp = _client(profile)
    review = review_core.parse_review(_claude(system, user, profile, client=sp))
    findings = review_core.finalize_findings(review["findings"], deterministic, ctx["n_benchmark"],
                                             min_benchmark=min_bench, eval01_severity=eval01_severity)
    gate = review_core.decide_gate(findings)
    try:
        # W2: a REAL eval-run via the typed Genie REST API (genie_create_eval_run/
        # genie_get_eval_run) — works from the deployed app (no `databricks` CLI on the Apps
        # container, unlike the legacy eval_gate.run_eval_gate this replaces). The Space being
        # reviewed lives in DEV, so this targets the SAME transport export_serialized just used
        # for the export step above: the dev-reader/writer SP under OBO (scope="dev-sp" — OBO
        # cannot span workspaces once the app is prod-hosted, ADR-0006), or the given profile
        # directly for local/offline callers with no user_token.
        eval_client = _client(scope="dev-sp") if user_token else _client(profile)
        eval_res = eval_gate.run_eval_gate_rest(space_id, client=eval_client)
    except Exception as e:  # noqa: BLE001 — never break the review; degrade to advisory
        eval_res = eval_gate.decide_eval(None, available=False,
                                         reason=f"eval-run indisponível neste ambiente ({e})")
    return {
        "findings": findings, "gate": gate, "eval": eval_res,
        "allowlist_violations": violations, "consumer_group": consumer_group,
        "access_spec": spec.to_dict(),
        "timeline": build_timeline(not violations, gate, eval_res, approved=False, deployed=False),
        "prod_serialized": prod_space,
        # The DEV-shaped export (what the promotion PR commits to src/; CI rebinds dev_->prod_).
        # Returned here so request_promotion reuses it instead of a second OBO export.
        "dev_serialized": dev_serialized,
    }


def can_approve(persona: str, requester: str, approver: str) -> tuple[bool, str]:
    """Separation of duties (S10/ADR-0002): the requester can never approve their own."""
    if persona != "steward":
        return False, "Apenas o Steward aprova promoções."
    if approver == requester:
        return False, "Segregação de funções: o solicitante não pode aprovar a própria promoção."
    return True, ""


def _secret(w: WorkspaceClient, scope: str, key: str) -> str:
    """Read a workspace secret value (the app SP has READ on the scope). Value is base64."""
    return base64.b64decode(w.secrets.get_secret(scope=scope, key=key).value).decode()


def _github_app(profile: str | None = None, *, client: WorkspaceClient | None = None) -> GitHubApp:
    """Build the GitHub App (bot) client from the secret scope, authenticating to Databricks as the
    APP SP (never the user) — the SP has READ on the scope. GitHub calls run as the bot."""
    w = client or _client(profile)  # app SP / local profile — NOT the user token
    owner, repo = GH_REPO.split("/", 1)
    return GitHubApp(
        owner=owner, repo=repo,
        app_id=_secret(w, GH_SECRET_SCOPE, "github_app_id"),
        installation_id=_secret(w, GH_SECRET_SCOPE, "github_installation_id"),
        private_key=_secret(w, GH_SECRET_SCOPE, "github_app_private_key"),
    )


def github_app_factory(profile: str | None = None) -> GitHubApp:
    """A no-arg-friendly factory for the bot client (app SP). Used by reconcile (LB4) to fetch audit
    facts lazily — only when attributing a new merge/approval — so the hot status poll pays nothing
    extra. Returns a fresh GitHubApp (token caching lives inside it)."""
    return _github_app(profile)


def request_promotion(space_id: str, profile: str | None = None, *, user_token: str | None = None,
                      requester_email: str | None = None, resource_title: str | None = None,
                      domain: str = DOMAIN, github: GitHubApp | None = None,
                      access_spec_: "access_spec.AccessSpec | None" = None,
                      rule_overrides: list[dict] | None = None,
                      table_mapping: "dict[str, str] | None" = None) -> dict:
    """GH2 (+ F2 + G7): open (or update) a real promotion PR for a space and post the attributed
    review comment.

    Flow: review the space (OBO export + app-SP reviewer; reused for the PR comment) → export the
    DEV-shaped serialized_space (OBO) → as the BOT, ensure the promotion branch, write the artifact
    to src/ (CI rebinds dev_->prod_), open-or-find the PR, and POST a new review comment mirroring
    the UI (steps + gate + findings/blockers) attributing the human requester — every re-request
    posts ANOTHER comment (never edits the prior one in place), so the PR keeps a review history.

    ``access_spec_`` (F2, optional) is the Requester's declared `AccessSpec` — DECLARATION is
    app-direct (this function just writes it to a committed sidecar), but ENFORCEMENT (actually
    applying UC grants + Genie Space permissions directly to each declared principal) is NOT done
    here: it goes through the governed pipeline (``scripts/apply_access.py``, run by CI/CD as the prod SP —
    mirrors GRANT-01's split between the app's PREVIEW check and CI's authoritative one). Named
    with a trailing underscore to avoid shadowing the ``access_spec`` module import.

    ``table_mapping`` (G7, optional) is the Requester's declared table de-para (source DEV ref ->
    desired prod ref overrides, keyed exactly like ``preview_promotion``'s ``source`` field) — ALSO
    only a DECLARATION here (committed to the new ``.mapping.json`` sidecar, see
    ``mapping_path_for``); the ACTUAL rebind is done by CI (``scripts/render.sh``'s
    ``apply-mapping``, run AFTER the plain dev_->prod_ rebind), and the prod allowlist stays
    UNWIDENED there — a mapped target outside ``prod_<domain>`` fails pr-checks (ENV-01), exactly
    like an un-mapped leak would. This is the ONE difference from rehydrate's G6 de-para, which
    widens its OWN allowlist for the caller's chosen catalogs — promotion never does, since CI (not
    this app) is what applies the mapping and must keep enforcing the strict target allowlist.

    Returns {review (UI fields), pr:{number,url}}. The PR opens regardless of findings — it's the
    fix/approval loop; pr-checks + the Steward gate (GH4) enforce the outcome.
    """
    full = review_space(space_id, profile=profile, user_token=user_token, domain=domain,
                        access_spec_=access_spec_, rule_overrides=rule_overrides)
    review = {k: full[k] for k in REVIEW_FIELDS}

    # Commit exactly the DEV-shaped export that was just reviewed (no second OBO export; closes the
    # TOCTOU window where the committed artifact could differ from the reviewed one).
    content = json.dumps(full["dev_serialized"], ensure_ascii=False, indent=2) + "\n"

    gh = github or _github_app(profile)  # the bot (app SP reads the scope)
    who = requester_email or "usuário autenticado"
    safe_id = "".join(c for c in space_id if c.isalnum() or c in "-_")  # title can't carry markup
    slug = space_slug(space_id)            # per-space: this space's own branch + committed file
    branch, path = branch_for(slug), src_path_for(slug)
    # G7: the prod Space name is now a Requester DECLARATION (pre-filled with the dev title by the
    # UI, editable) rather than a silent copy — the `.title` sidecar/render.sh/apply_access.py
    # convention it flows through is UNCHANGED, only the value's origin.
    prod_title = resource_title or safe_id
    # A per-space title sidecar so render.sh can name the generated prod genie_spaces resource (and so
    # the prod space keeps a friendly title). Committed next to the artifact; no shared manifest, so
    # concurrent per-space PRs never conflict.
    extra = {f"{GH_GENIE_SRC_DIR}/{slug}.title": prod_title + "\n"}
    # G9 (found live, PR #25): a re-request on this SAME branch/PR that no longer declares an
    # AccessSpec/table_mapping must CLEAR any sidecar a PRIOR round already committed there — extra
    # only ever UPSERTS, so without this a stale `.access.json`/`.mapping.json` would survive
    # forever once committed, and CI (check_grants.py/render.sh) would keep reading it.
    remove: list[str] = []
    spec = access_spec_ or access_spec.AccessSpec()
    if not spec.is_empty():
        # The AccessSpec sidecar (F2): committed alongside the artifact so CI's render.sh copies it
        # forward, check_grants.py (GRANT-01) verifies every declared principal, and
        # apply_access.py (governed enforcement) applies UC grants + Space permissions directly to
        # each declared principal — NEVER applied here (no app-direct prod mutation).
        extra[access_path_for(slug)] = json.dumps(spec.to_dict(), ensure_ascii=False, indent=2) + "\n"
    else:
        remove.append(access_path_for(slug))
    if table_mapping:
        # The table de-para sidecar (G7): committed alongside the artifact so CI's render.sh applies
        # it (AFTER the dev_->prod_ rebind, BEFORE the strict allowlist check) — NEVER applied here.
        extra[mapping_path_for(slug)] = json.dumps(table_mapping, ensure_ascii=False, indent=2) + "\n"
    else:
        remove.append(mapping_path_for(slug))
    pr = gh.open_or_update_promotion(
        branch=branch, path=path, content=content, extra_files=extra, remove_files=remove,
        title=f"Promover Genie Space para produção ({safe_id})",
        body=(f"Promoção solicitada por **{who}** via o app (bot abriu o PR em seu nome). "
              f"Espaço `{safe_id}` → `{path}`. Achados da revisão no comentário abaixo; `pr-checks` "
              f"é o gate automático e o Steward libera o deploy de produção (segregação de funções)."),
    )
    gh.post_review_comment(pr["number"], PROMOTION_COMMENT_MARKER,
                           render_promotion_comment(review, requester_email,
                                                    resource_title=prod_title, table_mapping=table_mapping))
    return {"review": review, "pr": {"number": pr["number"], "url": pr["html_url"]}, "branch": branch}


def promotion_status(number: int, profile: str | None = None, *, github: GitHubApp | None = None) -> dict:
    """GH3: the live state of a promotion PR (checks + merge + prod deploy/gate), read as the BOT
    (app SP) — no user token needed, the bot reads GitHub regardless of which user is viewing."""
    gh = github or _github_app(profile)
    return gh.get_status(number)


# --- F3: self-service access requests — GOVERNED grant application ----------
#
# An APPROVED access request must land its grant through the SAME governed path F2 already proved
# out (declare -> commit a sidecar -> bot PR -> scripts/apply_access.py runs the actual UC
# grant/Space-permission mutation, as the prod SP, behind pr-checks + the Steward's deploy gate).
# This module NEVER calls `w.grants`/`w.permissions` directly for an access request — doing so
# would be an app-direct prod mutation, exactly what F2/F3 exist to avoid. The only novelty here
# versus F2's promotion-time declaration is that the sidecar may ALREADY exist (from a prior
# promotion or a prior granted request) and must be MERGED into, never overwritten — an approved
# request should only ever ADD a principal, never silently drop one a promotion already declared.


def _merge_access_spec(existing: "access_spec.AccessSpec", *, principal_email: str,
                       want_space_permission: bool, space_permission_level: str,
                       want_uc_select: bool) -> "access_spec.AccessSpec":
    """Add ``principal_email`` to ``existing`` for whichever system(s) the request declared,
    without dropping anything already there (F3 acceptance: approval only ever GROWS access, same
    "never remove a declared principal" convention F2's own enforcement follows). Idempotent
    on the principal: re-approving/re-applying the same request twice is a no-op diff."""
    space_perms = list(existing.space_permissions)
    if want_space_permission and not any(
            sp.principal.name == principal_email and not sp.principal.is_group for sp in space_perms):
        space_perms.append(access_spec.SpacePermission(
            principal=access_spec.Principal(name=principal_email, is_group=False),
            level=space_permission_level))
    uc_principals = list(existing.uc_principals)
    if want_uc_select and not any(
            p.name == principal_email and not p.is_group for p in uc_principals):
        uc_principals.append(access_spec.Principal(name=principal_email, is_group=False))
    return access_spec.AccessSpec(space_permissions=tuple(space_perms), uc_principals=tuple(uc_principals))


def apply_access_request(*, space_id: str, principal_email: str, want_space_permission: bool,
                         space_permission_level: str, want_uc_select: bool,
                         resource_title: str | None = None, approver_email: str | None = None,
                         profile: str | None = None, github: GitHubApp | None = None) -> dict:
    """F3: apply an APPROVED access request via the GOVERNED path — commit the requesting
    principal into the target Space's ``<slug>.access.json`` sidecar (F2's declared-access model)
    and open/update a bot PR, so the ACTUAL grant/Space-permission mutation happens ONLY via
    ``scripts/apply_access.py`` in CI (behind pr-checks + the Steward's prod deploy gate) — never
    here. This function is app-direct ONLY in the git-committing sense (same split F2 already
    established between "declare" and "enforce"); it makes no `w.grants`/`w.permissions` call.

    Reads the sidecar's CURRENT content off ``main`` (if any — a space may already have one from a
    prior F2 promotion-time declaration, or a prior granted access request) and merges the new
    principal in via ``_merge_access_spec`` so nothing already declared is dropped. Opens on its
    OWN branch (``access_branch_for``, distinct from a promotion's ``branch_for``) so an in-flight
    promotion and an in-flight access request on the same Space never collide.

    Returns ``{pr: {number, url}, branch, access_spec}`` (the access_spec is the FULL merged
    dict, for the caller to persist/echo back). Raises ``GitHubError`` on any GitHub failure —
    callers must treat that as "approval recorded, application NOT yet done" (see
    ``access_request_store.mark_apply_failed``), never as a silent success.
    """
    gh = github or _github_app(profile)
    slug = space_slug(space_id)
    path = access_path_for(slug)
    branch = access_branch_for(slug)

    current_raw = gh.get_file_content(path)  # None if no sidecar exists yet on main
    current = access_spec.AccessSpec.from_dict(json.loads(current_raw)) if current_raw else access_spec.AccessSpec()
    merged = _merge_access_spec(
        current, principal_email=principal_email, want_space_permission=want_space_permission,
        space_permission_level=space_permission_level, want_uc_select=want_uc_select)
    content = json.dumps(merged.to_dict(), ensure_ascii=False, indent=2) + "\n"

    safe_id = "".join(c for c in space_id if c.isalnum() or c in "-_")
    who = approver_email or "um Steward"
    pr = gh.open_or_update_promotion(
        branch=branch, path=path, content=content,
        title=f"Conceder acesso a {principal_email} em {resource_title or safe_id}",
        body=(f"Solicitação de acesso self-service aprovada por **{who}**. Adiciona "
              f"**{principal_email}** ao AccessSpec declarado do Espaço `{safe_id}` "
              f"(`{path}`). A concessão real (permissão do Espaço / GRANT SELECT no UC) só é "
              f"aplicada por `scripts/apply_access.py`, executado pelo pipeline de CI/CD como o "
              f"service principal de produção, atrás do gate de deploy do Steward — este PR "
              f"apenas declara o acesso; nenhuma mutação de produção acontece aqui."),
    )
    return {"pr": {"number": pr["number"], "url": pr["html_url"]}, "branch": branch,
            "access_spec": merged.to_dict()}

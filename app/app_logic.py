"""Backend logic for the Acme promotion App (S8-S11) — testable, no Streamlit.

Wires the live Genie API + the proven engine (pre_render allowlist, Genie Reviewer,
eval gate) into the operations the UI needs:
  list_spaces(profile)                 -> the author's Genie Spaces
  review_space(space_id, ...)          -> the full promotion review (the hero flow):
      export -> pre-render dev_->prod_ -> deterministic allowlist and Audience check
      -> agent review (EVAL-01 deterministic) -> eval gate -> findings
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

import audience_check  # noqa: E402  (pilot AudienceSpec deterministic validation)
import audience_spec  # noqa: E402  (pilot Público do Space contract)
import authz  # noqa: E402  (A2 — verified identity + the live fail-closed access guard)
import change_request  # noqa: E402  (provider-neutral revision + observation contract)
import eval_gate  # noqa: E402
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


def src_path_for(slug: str) -> str:
    return f"{GH_GENIE_SRC_DIR}/{slug}.serialized_space.json"


def audience_path_for(slug: str) -> str:
    """Canonical required Público do Space sidecar (ADR-0009)."""
    return f"{GH_GENIE_SRC_DIR}/{slug}.audience.json"


def mapping_path_for(slug: str) -> str:
    """The per-space table de-para sidecar committed
    next to the artifact so CI's render.sh finds it by convention (`pre_render.py apply-mapping`,
    applied AFTER the dev_->prod_ rebind, BEFORE the strict allowlist check)."""
    return f"{GH_GENIE_SRC_DIR}/{slug}.mapping.json"


def revision_path_for(slug: str) -> str:
    """Self-describing immutable content/engine pair reviewed and deployed for this promotion."""
    return f"{GH_GENIE_SRC_DIR}/{slug}.revision.json"

# The UI-facing fields of a review (drop the large prod_serialized payload) — shared with the API.
REVIEW_FIELDS = ("findings", "gate", "eval", "timeline", "allowlist_violations",
                 "audience_spec")

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


def list_serving_endpoints(profile: str | None = None, *, client: WorkspaceClient | None = None) -> list[dict]:
    """S7a (app-ux-overhaul): every model-serving endpoint in the workspace (name only) — the
    live list the KA-endpoint registration admin UI picks from (RS1: never type an endpoint
    name/id, matching this app's established never-type-an-ID picker convention). A Knowledge
    Assistant endpoint IS a serving endpoint (RS1), so no separate KA-specific listing API is
    needed — the admin just picks the right one by name from this same list.

    Admin-only (gated at the API layer on `_is_admin`), same prod-local, no-caching pattern as
    `list_prod_spaces` — called fresh on every invocation so the picker always reflects what's
    actually deployable right now."""
    w = client or _client(profile)
    resp = w.serving_endpoints.list()
    return [{"name": e.name} for e in (resp or []) if e.name]


def list_principals(query: str = "", *, profile: str | None = None, client: WorkspaceClient | None = None,
                    user_token: str | None = None, kind: str = "all", limit: int = 25) -> list[dict]:
    """Users + groups of the workspace directory — the source for Audience and role pickers. Free
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

    """
    w = client or _client(profile, user_token=user_token)
    q = query.strip()
    principals: list[dict] = []

    if kind in ("all", "user"):
        user_filter = f'userName co "{q}" or displayName co "{q}"' if q else None
        count = 0
        for u in w.users.list(filter=user_filter):
            principals.append({"type": "user", "id": u.id,
                               "display": u.display_name or u.user_name or u.id,
                               "email": u.user_name})
            count += 1
            if count >= limit:
                break

    if kind in ("all", "group"):
        group_filter = f'displayName co "{q}"' if q else None
        count = 0
        for g in w.groups.list(filter=group_filter):
            display = g.display_name or g.id
            principals.append({"type": "group", "id": g.id, "display": display, "email": None})
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


def _query_ka_endpoint(serving_endpoint_name: str, question: str, profile: str,
                       client: WorkspaceClient | None = None) -> str:
    """S7b (app-ux-overhaul): query a registered Knowledge Assistant endpoint — a single USER turn
    (no system prompt: the KA's OWN indexed knowledge grounds it, not this app's reviewer persona).

    Agent Bricks Knowledge Assistants serve the **Responses API** (`task: agent/v1/responses`), NOT
    Chat Completions — proven live 2026-07-16: `serving_endpoints.query(messages=...)` is rejected
    ("'messages' field is not supported. Please use 'input' field instead"). So we POST the Responses
    shape `{"input": [{"role","content"}]}` to `/serving-endpoints/{name}/invocations` and read back
    `output[].content[].output_text` (the assistant message may arrive as several text segments +
    citation annotations — we concatenate the text). `_ka_advisory_findings` catches ANY failure
    uniformly (timeout, 429, 5xx, not-ready), so this stays a best-effort advisory call."""
    w = client or _client(profile)
    resp = w.api_client.do(
        "POST", f"/serving-endpoints/{serving_endpoint_name}/invocations",
        body={"input": [{"role": "user", "content": question}]},
    )
    return _extract_responses_text(resp)


def _extract_responses_text(resp: dict) -> str:
    """Pull the assistant's plain text out of a Responses-API payload. The KA returns
    `output`: a list of items; the assistant message item (`type == "message"`, `role ==
    "assistant"`) carries `content`: a list of segments, each `{"type": "output_text", "text": ...}`
    (plus optional citation annotations we don't surface). Concatenate every output_text segment
    across every message item. Tolerant of shape drift: a segment whose `content` is a plain string,
    or a top-level `output_text`, are both handled; anything unrecognized yields ''."""
    if not isinstance(resp, dict):
        return ""
    parts: list[str] = []
    for item in resp.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for seg in content or []:
            if isinstance(seg, dict) and seg.get("type") == "output_text" and seg.get("text"):
                parts.append(seg["text"])
            elif isinstance(seg, str):
                parts.append(seg)
    text = "".join(parts).strip()
    if text:
        return text
    # Fallbacks for minor shape variations seen across serving versions.
    if isinstance(resp.get("output_text"), str):
        return resp["output_text"].strip()
    return ""


def _ka_question(ctx: dict) -> str:
    """The one-shot governance Q&A sent to each in-scope KA endpoint — summarizes the space
    being promoted and asks the KA (grounded on whatever it's indexed, e.g. a handbook) whether
    it sees anything worth flagging. D5: purely advisory input, never a rule the reviewer must
    obey — the answer is surfaced as-is, not merged into the reviewer's own judgment."""
    tables = ", ".join(t["identifier"] for t in ctx.get("tables", [])) or "(nenhuma)"
    instructions = " | ".join(ctx.get("instructions", [])[:3]) or "(nenhuma)"
    return (
        "Este Genie Space está sendo promovido para produção. Com base no conhecimento indexado, "
        "há alguma preocupação de governança, convenção ou boa prática relevante para este espaço? "
        f"Tabelas: {tables}. Instruções: {instructions}."
    )


def _ka_advisory_findings(ka_endpoints: list[dict] | None, ctx: dict, profile: str,
                          client: WorkspaceClient | None = None) -> list[dict]:
    """S7b: one advisory finding per in-scope, enabled KA endpoint (D5 — additive only, NEVER a
    BLOCKER; `ka_endpoints` is already filtered to this space's scope by the caller via
    `KaEndpointsStore.list_enabled_for_space_dicts`). A query failure degrades to a quiet,
    non-blocking notice (GR1) rather than a silent skip OR a broken review — caught broadly
    (any Exception), matching this module's existing best-effort pattern for the grant-check
    preview above."""
    if not ka_endpoints:
        return []
    question = _ka_question(ctx)
    findings = []
    for ep in ka_endpoints:
        rule_id = f"KA:{ep['name']}"
        try:
            answer = _query_ka_endpoint(ep["serving_endpoint_name"], question, profile, client=client)
            findings.append({
                "severity": "SUGGESTION", "rule_id": rule_id,
                "citation": f"Knowledge Assistant › {ep['name']}",
                "message": answer or "(sem resposta)",
                "suggestion": None,
            })
        except Exception as e:  # noqa: BLE001 — advisory-only; a KA outage must never break the review
            findings.append({
                "severity": "STYLE", "rule_id": rule_id,
                "citation": f"Knowledge Assistant › {ep['name']}",
                "message": f"Knowledge Assistant '{ep['name']}' indisponível — consulta ignorada ({e}).",
                "suggestion": None,
            })
    return findings


_VALIDATION_FIXTURE_CTX = {
    "tables": [], "instructions": ["Validação de template — smoke test, sem espaço real."],
    "example_sqls": [], "benchmark_questions": [], "benchmark_sqls": [], "n_benchmark": 0, "joins": [],
}


def validate_persona_template(template_text: str, profile: str, client: WorkspaceClient | None = None) -> None:
    """S8: the save-time guardrail GR2 calls for — runs ONE real test review (a tiny fixture
    context, no real Genie Space needed since `build_review_prompt` only needs a `ctx` dict) with
    the CANDIDATE persona template, and raises `ValueError` (-> 400 at the API layer, same
    convention as every other store's input validation) if the raw response isn't even
    parseable JSON. `PROTECTED_CORE` is still appended as always (this exercises the exact same
    `build_review_prompt` path a real review would) — this only catches a persona edit that
    derails the LLM badly enough to break output parsing, not a template that's merely a bad
    idea stylistically."""
    system, user = review_core.build_review_prompt(
        _VALIDATION_FIXTURE_CTX, handbook_rules.RULES, persona_template=template_text)
    sp = client or _client(profile)
    raw = _claude(system, user, profile, client=sp)
    if not review_core.raw_is_parseable(raw):
        raise ValueError(
            "este template fez o revisor responder com algo que não é JSON válido — "
            "ajuste o texto e tente novamente.")


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


def _pii_audience_findings(space: dict, spec: "audience_spec.AudienceSpec | None") -> list[dict]:
    """Advisory-only PII expansion note using the pilot's audience vocabulary."""
    if spec is None:
        return []
    flagged_cols: list[str] = []
    for table in (space.get("data_sources") or {}).get("tables", []) or []:
        for column in table.get("column_configs") or []:
            name = (column.get("column_name") or "").lower()
            if any(term in name for term in handbook_rules.PII_SIGNAL_TERMS):
                flagged_cols.append(f"{table.get('identifier', '')}.{column.get('column_name')}")
    if not flagged_cols:
        return []
    return [{
        "severity": "SUGGESTION", "rule_id": "PII-01",
        "citation": "Genie Promotion Handbook › PII › PII-01",
        "message": (f"o Público do Space ({', '.join(spec.names())}) alcança colunas com sinal de "
                    f"PII/sigilo bancário ({', '.join(flagged_cols)}) — confirme máscaras e row filters."),
        "suggestion": "Revise as proteções UC antes da aprovação; o app não concede acesso aos dados.",
    }]


def _audience_principal_exists(w: WorkspaceClient, name: str, is_group: bool) -> bool:
    if is_group:
        return bool(list(w.groups.list(filter=f'displayName eq "{name}"')))
    return bool(list(w.users.list(filter=f'userName eq "{name}"')))


def review_space(space_id: str, profile: str | None = None, to_env: str = "prod", domain: str = DOMAIN,
                 grant_profile: str | None = None,
                 user_token: str | None = None,
                 audience_spec_: "audience_spec.AudienceSpec | None" = None,
                 rule_overrides: list[dict] | None = None,
                 ka_endpoints: list[dict] | None = None,
                 persona_template: str | None = None) -> dict:
    """The hero flow: export -> pre-render -> allowlist + Audience validation
    -> agent review (EVAL-01 deterministic) -> eval gate. Deterministic findings own their
    rule_id (no double-count). Returns findings/gate/eval/allowlist_violations/timeline/prod_serialized.

    ``rule_overrides`` (G2, optional) is the raw admin-configured override rows from
    `app/rules_store.py` (`RulesStore.list_all_dicts()`), or `None` when no store is bound —
    `rules_config.effective_rules`/`eval01_config` degrade to `handbook_rules.RULES`'s hardcoded
    defaults in that case, so a caller with no store behaves EXACTLY as before this rule ever
    existed.

    ``ka_endpoints`` (S7b, optional) is this space's IN-SCOPE, ENABLED Knowledge Assistant
    endpoints — `app/ka_endpoints_store.py`'s `KaEndpointsStore.list_enabled_for_space_dicts
    (space_id)`, or `None`/empty when none are registered/in-scope. Each produces ONE additive
    advisory finding (never a BLOCKER, D5) via `_ka_advisory_findings`; a query failure degrades
    to a quiet notice finding rather than breaking the review (GR1) — this never affects the
    gate the way `deterministic` findings do.

    ``persona_template`` (S8, optional) is the admin-saved custom system-prompt persona/policy
    text (`app/prompt_template_store.py`'s `PromptTemplateStore.get().template_text`), or `None`
    when nothing's been saved — `review_core.build_review_prompt` falls back to
    `DEFAULT_PERSONA` in that case. Either way, `PROTECTED_CORE` (the prompt-injection defense +
    JSON output schema) is ALWAYS appended, never overridable.

    Live Audience inspection runs only when an explicit reachable prod target is configured;
    the authoritative check also runs in content-repository CI.

    Cross-workspace rewiring (A2 / ADR-0006 half-migration fix): the export step is delegated to
    ``export_serialized`` UNCLIENTED (no ``client=`` injected here) so it does its own identity
    verification + ``assert_can_access`` gating + dev-sp transport selection when ``user_token`` is
    set — this function no longer builds an OBO client for the export itself (OBO cannot reach dev
    once the app is prod-hosted). Locally (``profile``, no token) the export still runs against the
    given profile, unchanged.
    """
    if audience_spec_ is None:
        raise ValueError("AudienceSpec is required")
    grant_profile = grant_profile or os.environ.get("APP_PROD_PROFILE")  # None -> skip preview
    # export_serialized resolves its OWN transport: dev-sp + assert_can_access(verified identity)
    # when user_token is set (the only way to reach dev post-ADR-0006), else the local profile.
    dev_serialized = export_serialized(space_id, profile, user_token=user_token)
    rendered = pre_render.rebind(json.dumps(dev_serialized, ensure_ascii=False), "dev", to_env, domain)
    prod_space = json.loads(rendered)
    violations = pre_render.find_violations(rendered, to_env, domain)  # deterministic ENV allowlist

    # Deterministic findings own
    # their rule_id via finalize_findings, so the LLM can't soften them and they aren't duplicated.
    deterministic: list[dict] = [
        {"severity": "BLOCKER", "rule_id": "ENV-01",
         "citation": "Genie Promotion Handbook › Catalog-per-Env › ENV-01",
         "message": f"referência a catálogo fora de {to_env}_{domain}: {v}",
         "suggestion": "Remover/pré-renderizar a referência de outro ambiente."}
        for v in violations
    ]
    # Missing SELECT is informational; invalid live inputs block; inspection outages are operational.
    if grant_profile:
        gw = _client(grant_profile)
        deterministic += audience_check.check_audience(
            prod_space, audience_spec_,
            lambda fq: _effective_grants(grant_profile, fq, client=gw),
            principal_exists=lambda name, is_group: _audience_principal_exists(gw, name, is_group),
            table_exists=lambda fq: bool(gw.tables.exists(full_name=fq)),
        )
    deterministic += _pii_audience_findings(prod_space, audience_spec_)

    # G2: the EFFECTIVE rule set (hardcoded RULES + admin overrides/custom rules) grounds the
    # prompt; EVAL-01's threshold/severity backstop is resolved the same way (finalize_findings
    # owns that check deterministically — see rules_config's docstring for why). The eval-RUN
    # pass-rate threshold (a distinct gate — see eval_run_threshold's docstring) is resolved from
    # the same override row, then fed to run_eval_gate_rest below.
    effective = rules_config.effective_rules(rule_overrides)
    min_bench, eval01_severity = rules_config.eval01_config(rule_overrides)
    eval_threshold = rules_config.eval_run_threshold(rule_overrides)
    ctx = review_core.build_space_context(prod_space)
    system, user = review_core.build_review_prompt(ctx, effective, deterministic,
                                                    persona_template=persona_template)
    # LLM reviewer = shared operation -> app SP / local profile (a serving scope isn't part of
    # OBO; the reviewer agent doesn't read the user's data, and it never runs against dev).
    sp = _client(profile)
    review = review_core.parse_review(_claude(system, user, profile, client=sp))
    findings = review_core.finalize_findings(review["findings"], deterministic, ctx["n_benchmark"],
                                             min_benchmark=min_bench, eval01_severity=eval01_severity)
    # The eval-RUN (Genie re-runs its own benchmark questions) is computed BEFORE the gate so a
    # `block` verdict (pass-rate < threshold) can gate the promotion — a failed eval-run is a real
    # quality BLOCKER, not just a red pipeline node. `advisory` (eval-run unavailable, or no
    # benchmark questions) stays non-blocking (graceful degradation). Runs against the DEV space via
    # the same transport as the export (dev-reader SP under OBO once prod-hosted, ADR-0006; or the
    # given profile locally). Any failure degrades to advisory — never breaks the review.
    try:
        eval_client = _client(scope="dev-sp") if user_token else _client(profile)
        eval_res = eval_gate.run_eval_gate_rest(space_id, client=eval_client, threshold=eval_threshold)
    except Exception as e:  # noqa: BLE001 — never break the review; degrade to advisory
        eval_res = eval_gate.decide_eval(None, eval_threshold, available=False,
                                         reason=f"eval-run indisponível neste ambiente ({e})")
    # A blocking eval-run becomes a deterministic EVAL-RUN BLOCKER finding, so decide_gate below
    # turns the whole gate to `failure` (the app then shows "promoção bloqueada" and offers no
    # merge). It's a distinct rule_id from EVAL-01 (which is the ">= N benchmark questions" COUNT
    # check); this is the RUN's pass-rate. Appended before decide_gate so it counts as a BLOCKER.
    if eval_res.get("status") == "block":
        findings.append({
            "severity": "BLOCKER", "rule_id": "EVAL-RUN",
            "citation": "Genie Promotion Handbook › Quality › EVAL-RUN",
            "message": eval_res.get("summary")
            or "O eval-run do Genie ficou abaixo do limiar de acerto — promoção bloqueada.",
            "suggestion": "Revise instruções/exemplos do espaço e reexecute os benchmarks até "
                          "passar do limiar antes de promover.",
        })
    # S7b: additive-only KA advisory findings — appended AFTER the deterministic + eval findings own
    # their rule_ids, so decide_gate sees them but they can never win a BLOCKER (every KA finding is
    # SUGGESTION or, on failure, STYLE — never BLOCKER, D5).
    findings = findings + _ka_advisory_findings(ka_endpoints, ctx, profile, client=sp)
    gate = review_core.decide_gate(findings)
    return {
        "findings": findings, "gate": gate, "eval": eval_res,
        "allowlist_violations": violations,
        "audience_spec": audience_spec_.to_dict(),
        "timeline": build_timeline(not violations, gate, eval_res, approved=False, deployed=False),
        "prod_serialized": prod_space,
        # The DEV-shaped export (what the promotion PR commits to src/; CI rebinds dev_->prod_).
        # Returned here so request_promotion reuses it instead of a second OBO export.
        "dev_serialized": dev_serialized,
    }


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
                      audience_spec_: "audience_spec.AudienceSpec | None" = None,
                      rule_overrides: list[dict] | None = None,
                      ka_endpoints: list[dict] | None = None,
                      persona_template: str | None = None,
                      table_mapping: "dict[str, str] | None" = None) -> dict:
    """GH2 (+ F2 + G7): open (or update) a real promotion PR for a space and post the attributed
    review comment.

    Flow: review the space (OBO export + app-SP reviewer; reused for the PR comment) → export the
    DEV-shaped serialized_space (OBO) → as the BOT, ensure the promotion branch, write the artifact
    to src/ (CI rebinds dev_->prod_), open-or-find the PR, and POST a new review comment mirroring
    the UI (steps + gate + findings/blockers) attributing the human requester — every re-request
    posts ANOTHER comment (never edits the prior one in place), so the PR keeps a review history.

    ``table_mapping`` (G7, optional) is the Requester's declared table de-para (source DEV ref ->
    desired prod ref overrides, keyed exactly like ``preview_promotion``'s ``source`` field) — ALSO
    only a DECLARATION here (committed to the new ``.mapping.json`` sidecar, see
    ``mapping_path_for``); the ACTUAL rebind is done by CI (``scripts/render.sh``'s
    ``apply-mapping``, run AFTER the plain dev_->prod_ rebind), and the prod allowlist stays
    UNWIDENED there — a mapped target outside ``prod_<domain>`` fails pr-checks (ENV-01), exactly
    like an un-mapped leak would. This is the ONE difference from rehydrate's G6 de-para, which
    widens its OWN allowlist for the caller's chosen catalogs — promotion never does, since CI (not
    this app) is what applies the mapping and must keep enforcing the strict target allowlist.

    Returns {review (UI fields), pr:{number,url}|None}. A content BLOCKER stops before GitHub: the
    author fixes the named item in Dev and re-reviews, so no Change Request exists for knowingly
    invalid content. Passing review continues through the immutable PR + CI + Steward gates.
    """
    if audience_spec_ is None:
        raise ValueError("AudienceSpec is required")
    full = review_space(space_id, profile=profile, user_token=user_token, domain=domain,
                        audience_spec_=audience_spec_, rule_overrides=rule_overrides,
                        ka_endpoints=ka_endpoints, persona_template=persona_template)
    full.setdefault("audience_spec", audience_spec_.to_dict())
    review = {k: full[k] for k in REVIEW_FIELDS}

    # Decision-first pilot flow: content findings are resolved before a Change Request exists.
    # This is still entirely pre-production (review/export reads only); no branch, PR, comment,
    # Lakebase Promotion or deploy Attempt is created for a blocked review.
    if (review.get("gate") or {}).get("conclusion") == "failure":
        return {"review": review, "pr": None, "branch": None, "no_change": False,
                "blocked": True}

    # Commit exactly the DEV-shaped export that was just reviewed (no second OBO export; closes the
    # TOCTOU window where the committed artifact could differ from the reviewed one).
    content = json.dumps(full["dev_serialized"], ensure_ascii=False, indent=2) + "\n"

    gh = github or _github_app(profile)  # the bot (app SP reads the scope)
    who = requester_email or "usuário autenticado"
    safe_id = "".join(c for c in space_id if c.isalnum() or c in "-_")  # title can't carry markup
    slug = space_slug(space_id)            # per-space: this space's own branch + committed file
    branch, path = branch_for(slug), src_path_for(slug)
    # G7: the prod Space name is now a Requester DECLARATION (pre-filled with the dev title by the
    # UI, editable) rather than a silent copy. The `.title` sidecar carries it into rendering.
    prod_title = resource_title or safe_id
    # A per-space title sidecar so render.sh can name the generated prod genie_spaces resource (and so
    # the prod space keeps a friendly title). Committed next to the artifact; no shared manifest, so
    # concurrent per-space PRs never conflict.
    extra = {f"{GH_GENIE_SRC_DIR}/{slug}.title": prod_title + "\n"}
    # A re-request on this SAME branch/PR clears a stale table-mapping sidecar.
    remove: list[str] = []
    extra[audience_path_for(slug)] = json.dumps(
        audience_spec_.to_dict(), ensure_ascii=False, indent=2) + "\n"
    if table_mapping:
        # The table de-para sidecar (G7): committed alongside the artifact so CI's render.sh applies
        # it (AFTER the dev_->prod_ rebind, BEFORE the strict allowlist check) — NEVER applied here.
        extra[mapping_path_for(slug)] = json.dumps(table_mapping, ensure_ascii=False, indent=2) + "\n"
    else:
        remove.append(mapping_path_for(slug))
    # ADR-0008: bind the desired content tree to the immutable engine commit selected by the
    # content repository. The manifest is excluded from its own digest and then committed beside
    # the artifact, so PR checks and deploy can prove they consumed the same pair after merge.
    engine_revision = change_request.parse_engine_lock(gh.get_file_content("engine.lock"))
    content_revision = change_request.compute_content_revision(
        {path: content, **extra}, remove
    )
    revisions = change_request.RevisionPair(content_revision, engine_revision)
    extra[revision_path_for(slug)] = json.dumps(
        {"version": 1, "revisions": revisions.to_dict()},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    pr = gh.open_or_update_promotion(
        branch=branch, path=path, content=content, extra_files=extra, remove_files=remove,
        title=f"Promover Genie Space para produção ({safe_id})",
        body=(f"Promoção solicitada por **{who}** via o app (bot abriu o PR em seu nome). "
              f"Espaço `{safe_id}` → `{path}`. Achados da revisão no comentário abaixo; `pr-checks` "
              f"é o gate automático e o Steward libera o deploy de produção (segregação de funções)."),
    )
    # No-op promotion: the space is already in prod byte-identical (nothing to promote). No PR was
    # opened — surface it so the app tells the user instead of showing an empty pipeline. The review
    # still ran (it's informative), so we return it; there's just no PR to track.
    if pr.get("no_change"):
        return {"review": review, "pr": None, "branch": branch, "no_change": True,
                "revisions": revisions.to_dict()}
    gh.post_review_comment(pr["number"], PROMOTION_COMMENT_MARKER,
                           render_promotion_comment(review, requester_email,
                                                    resource_title=prod_title,
                                                    table_mapping=table_mapping,
                                                    revisions=revisions.to_dict()))
    return {"review": review, "pr": {"number": pr["number"], "url": pr["html_url"]}, "branch": branch,
            "no_change": False, "change_request": {
                "provider": "github", "external_id": str(pr["number"]),
                "external_url": pr["html_url"], **revisions.to_dict(),
            }}


def promotion_status(number: int, profile: str | None = None, *, github: GitHubApp | None = None,
                     approved_revisions: dict | None = None,
                     include_deployment_evidence: bool = False) -> dict:
    """GH3: the live state of a promotion PR (checks + merge + prod deploy/gate), read as the BOT
    (app SP) — no user token needed, the bot reads GitHub regardless of which user is viewing."""
    gh = github or _github_app(profile)
    return gh.get_status(
        number,
        approved_revisions=approved_revisions,
        include_deployment_evidence=include_deployment_evidence,
    )

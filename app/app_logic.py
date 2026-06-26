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

import eval_gate  # noqa: E402
import grant_check  # noqa: E402
import handbook_rules  # noqa: E402
import pre_render  # noqa: E402
import review_core  # noqa: E402
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

# The UI-facing fields of a review (drop the large prod_serialized payload) — shared with the API.
REVIEW_FIELDS = ("findings", "gate", "eval", "timeline", "allowlist_violations", "consumer_group")


def _client(profile: str | None = None, *, user_token: str | None = None) -> WorkspaceClient:
    """Build a Databricks SDK WorkspaceClient for one of three auth contexts.

    - ``user_token`` (deployed app, OBO): act *as the logged-in user* via their forwarded
      OAuth token (the ``x-forwarded-access-token`` header). Host comes from the
      auto-injected env. Used for user-facing reads so the app respects the user's own
      data access (and the audit trail is per-user), never the app's broader rights.
    - ``profile`` (local dev): select a workspace from ``~/.databrickscfg``.
    - neither (deployed app, shared ops): the app's service principal authenticates
      automatically via injected env — used for the LLM reviewer + the prod grant preview.

    Construction is lazy (no network call), so this is safe to build offline.
    """
    if user_token:
        # auth_type="pat" so the SDK uses ONLY the forwarded user token. Without it the app
        # SP's auto-injected OAuth env (DATABRICKS_CLIENT_ID/SECRET) collides with the token
        # -> "more than one authorization method configured: oauth and pat".
        return WorkspaceClient(host=Config().host, token=user_token, auth_type="pat")
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def list_spaces(profile: str | None = None, *, client: WorkspaceClient | None = None,
                user_token: str | None = None) -> list[dict]:
    """The author's Genie Spaces (id + title). Acts as the logged-in user via ``user_token``
    (OBO) on the deployed app, or a local ``profile``. ``client`` is injectable for tests."""
    w = client or _client(profile, user_token=user_token)
    resp = w.genie.list_spaces()
    return [{"space_id": s.space_id, "title": s.title or "(sem título)"}
            for s in (resp.spaces or [])]


def export_serialized(space_id: str, profile: str, client: WorkspaceClient | None = None) -> dict:
    w = client or _client(profile)
    space = w.genie.get_space(space_id, include_serialized_space=True)
    ss = space.serialized_space
    return json.loads(ss) if isinstance(ss, str) else (ss or {})


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


def review_space(space_id: str, profile: str | None = None, to_env: str = "prod", domain: str = DOMAIN,
                 consumer_group: str | None = None, grant_profile: str | None = None,
                 user_token: str | None = None) -> dict:
    """The hero flow: export -> pre-render -> allowlist (ENV-01) + grant check (GRANT-01)
    -> agent review (EVAL-01 deterministic) -> eval gate. Deterministic findings own their
    rule_id (no double-count). Returns findings/gate/eval/allowlist_violations/timeline/prod_serialized.

    The in-app GRANT-01 preview only runs when an explicit, reachable prod target is
    configured (grant_profile arg or APP_PROD_PROFILE locally) — the prod catalog is
    workspace-isolated, so the dev-hosted app can't see it. On the deployed app it's skipped;
    the AUTHORITATIVE GRANT-01 runs in CI (pr-checks) as the prod SP (scripts/check_grants.py).
    """
    consumer_group = consumer_group or os.environ.get("APP_CONSUMER_GROUP", "account users")
    grant_profile = grant_profile or os.environ.get("APP_PROD_PROFILE")  # None -> skip preview
    # Auth split (see _client): `w` acts as the user (OBO) / local profile for the Genie
    # export so we respect the user's own access; `gw` (built in the try) and the LLM
    # reviewer run as the app SP — shared/cross-workspace ops that aren't user data.
    w = _client(profile, user_token=user_token)
    dev_serialized = export_serialized(space_id, profile, client=w)
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
    if grant_profile:  # only preview when a reachable prod target is configured (else CI owns it)
        try:
            gw = _client(grant_profile)  # built here so a bad/unreachable prod profile can't break the review
            deterministic += grant_check.check_grants(
                prod_space, consumer_group, lambda fq: _effective_grants(grant_profile, fq, client=gw))
        except Exception:  # noqa: BLE001 — grant check is best-effort; never break the review
            pass

    ctx = review_core.build_space_context(prod_space)
    system, user = review_core.build_review_prompt(ctx, handbook_rules.RULES, deterministic)
    # LLM reviewer = shared operation -> app SP (a serving scope isn't part of OBO; the
    # reviewer agent doesn't read the user's data). Locally (no OBO) `w` is the profile client.
    sp = _client(profile) if user_token else w
    review = review_core.parse_review(_claude(system, user, profile, client=sp))
    findings = review_core.finalize_findings(review["findings"], deterministic, ctx["n_benchmark"])
    gate = review_core.decide_gate(findings)
    try:
        # eval_gate still uses the `databricks` CLI — absent on the Apps container, and
        # profile is None under OBO. It's advisory by design, so degrade (never break the
        # review). The authoritative eval-run gate runs in CI with a real profile.
        eval_res = eval_gate.run_eval_gate(space_id, profile)
    except Exception:  # noqa: BLE001
        eval_res = eval_gate.decide_eval(None, available=False,
                                         reason="eval-run indisponível neste ambiente (sem CLI)")
    return {
        "findings": findings, "gate": gate, "eval": eval_res,
        "allowlist_violations": violations, "consumer_group": consumer_group,
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
                      domain: str = DOMAIN, github: GitHubApp | None = None) -> dict:
    """GH2: open (or update) a real promotion PR for a space and post the attributed review comment.

    Flow: review the space (OBO export + app-SP reviewer; reused for the PR comment) → export the
    DEV-shaped serialized_space (OBO) → as the BOT, ensure the promotion branch, write the artifact
    to src/ (CI rebinds dev_->prod_), open-or-find the PR, and upsert one comment that mirrors the
    UI (steps + gate + findings/blockers) attributing the human requester.

    Returns {review (UI fields), pr:{number,url}}. The PR opens regardless of findings — it's the
    fix/approval loop; pr-checks + the Steward gate (GH4) enforce the outcome.
    """
    full = review_space(space_id, profile=profile, user_token=user_token, domain=domain)
    review = {k: full[k] for k in REVIEW_FIELDS}

    # Commit exactly the DEV-shaped export that was just reviewed (no second OBO export; closes the
    # TOCTOU window where the committed artifact could differ from the reviewed one).
    content = json.dumps(full["dev_serialized"], ensure_ascii=False, indent=2) + "\n"

    gh = github or _github_app(profile)  # the bot (app SP reads the scope)
    who = requester_email or "usuário autenticado"
    safe_id = "".join(c for c in space_id if c.isalnum() or c in "-_")  # title can't carry markup
    slug = space_slug(space_id)            # per-space: this space's own branch + committed file
    branch, path = branch_for(slug), src_path_for(slug)
    # A per-space title sidecar so render.sh can name the generated prod genie_spaces resource (and so
    # the prod space keeps a friendly title). Committed next to the artifact; no shared manifest, so
    # concurrent per-space PRs never conflict.
    extra = {f"{GH_GENIE_SRC_DIR}/{slug}.title": (resource_title or safe_id) + "\n"}
    pr = gh.open_or_update_promotion(
        branch=branch, path=path, content=content, extra_files=extra,
        title=f"Promover Genie Space para produção ({safe_id})",
        body=(f"Promoção solicitada por **{who}** via o app (bot abriu o PR em seu nome). "
              f"Espaço `{safe_id}` → `{path}`. Achados da revisão no comentário abaixo; `pr-checks` "
              f"é o gate automático e o Steward libera o deploy de produção (segregação de funções)."),
    )
    gh.upsert_comment(pr["number"], PROMOTION_COMMENT_MARKER,
                      render_promotion_comment(review, requester_email))
    return {"review": review, "pr": {"number": pr["number"], "url": pr["html_url"]}, "branch": branch}


def promotion_status(number: int, profile: str | None = None, *, github: GitHubApp | None = None) -> dict:
    """GH3: the live state of a promotion PR (checks + merge + prod deploy/gate), read as the BOT
    (app SP) — no user token needed, the bot reads GitHub regardless of which user is viewing."""
    gh = github or _github_app(profile)
    return gh.get_status(number)

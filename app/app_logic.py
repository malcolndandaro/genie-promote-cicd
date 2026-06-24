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
from databricks.sdk.service.catalog import SecurableType
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("genie_reviewer", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import eval_gate  # noqa: E402
import grant_check  # noqa: E402
import handbook_rules  # noqa: E402
import pre_render  # noqa: E402
import review_core  # noqa: E402

LLM = os.environ.get("APP_LLM_ENDPOINT", "databricks-claude-opus-4-8")
DOMAIN = os.environ.get("APP_DOMAIN", "recebiveis")


def _client(profile: str | None = None) -> WorkspaceClient:
    """Build a Databricks SDK WorkspaceClient.

    Locally a CLI profile (``~/.databrickscfg``) selects the workspace. On the
    deployed Databricks App ``profile`` is None and auth is automatic — the app's
    service principal is injected via env, so ``WorkspaceClient()`` just works.
    Construction is lazy (no network call), so this is safe to build offline.
    """
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def list_spaces(profile: str, client: WorkspaceClient | None = None) -> list[dict]:
    """The author's Genie Spaces (id + title). ``client`` is injectable for tests."""
    w = client or _client(profile)
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


def review_space(space_id: str, profile: str, to_env: str = "prod", domain: str = DOMAIN,
                 consumer_group: str | None = None, grant_profile: str | None = None) -> dict:
    """The hero flow: export -> pre-render -> allowlist (ENV-01) + grant check (GRANT-01)
    -> agent review (EVAL-01 deterministic) -> eval gate. Deterministic findings own their
    rule_id (no double-count). Returns findings/gate/eval/allowlist_violations/timeline/prod_serialized.

    GRANT-01 queries the TARGET (prod) workspace — the prod catalog is workspace-isolated
    from dev — so it uses grant_profile (APP_PROD_PROFILE), falling back to `profile`.
    Best-effort: if prod isn't reachable, the grant check is skipped (no false finding).
    """
    consumer_group = consumer_group or os.environ.get("APP_CONSUMER_GROUP", "account users")
    grant_profile = grant_profile or os.environ.get("APP_PROD_PROFILE", profile)
    # One client per workspace, reused across calls: `w` for the source/dev ops (export +
    # agent review) and `gw` for the prod grant check (built lazily inside the try below).
    w = _client(profile)
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
    try:
        gw = _client(grant_profile)  # built here so a bad/unreachable prod profile can't break the review
        deterministic += grant_check.check_grants(
            prod_space, consumer_group, lambda fq: _effective_grants(grant_profile, fq, client=gw))
    except Exception:  # noqa: BLE001 — grant check is best-effort; never break the review
        pass

    ctx = review_core.build_space_context(prod_space)
    system, user = review_core.build_review_prompt(ctx, handbook_rules.RULES, deterministic)
    review = review_core.parse_review(_claude(system, user, profile, client=w))
    findings = review_core.finalize_findings(review["findings"], deterministic, ctx["n_benchmark"])
    gate = review_core.decide_gate(findings)
    eval_res = eval_gate.run_eval_gate(space_id, profile)
    return {
        "findings": findings, "gate": gate, "eval": eval_res,
        "allowlist_violations": violations, "consumer_group": consumer_group,
        "timeline": build_timeline(not violations, gate, eval_res, approved=False, deployed=False),
        "prod_serialized": prod_space,
    }


def can_approve(persona: str, requester: str, approver: str) -> tuple[bool, str]:
    """Separation of duties (S10/ADR-0002): the requester can never approve their own."""
    if persona != "steward":
        return False, "Apenas o Steward aprova promoções."
    if approver == requester:
        return False, "Segregação de funções: o solicitante não pode aprovar a própria promoção."
    return True, ""

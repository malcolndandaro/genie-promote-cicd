"""Deterministic GRANT-01 check (S5, extended F2, apply_access-aware F2b/G9): does every declared
principal have SELECT on every data_source table of the (pre-rendered, prod) serialized_space?

This is the hero finding — a naming/schema linter can't see it; it needs UC state.
The check FINDS the gap deterministically; the agent then cites it as a grounded,
friendly GRANT-01 finding. Pure: the grants getter is injected, so it's unit-testable
without a workspace. The live shell injects effective-privileges lookups (SELECT can
cascade from a schema/catalog grant, so table-level grants alone would false-positive).

F2 extends the original single-consumer-group check to ALL principals declared in an `AccessSpec`
(every group/user from either the Space-permissions or the UC-grants half — see
`access_spec.AccessSpec.all_principals()`): a principal that can open a Space but can't read its
data is the exact "unusable Space" failure GRANT-01 exists to catch, so it's checked the same way
regardless of which half of the spec declared it.

G9 (stakeholder decision, 2026-07-13): the pipeline's OWN `apply_access` step (scripts/apply_access.py,
post-merge) grants every DECLARED principal at deploy time — so blocking the PR pre-merge on
something the pipeline is about to do anyway was contradictory. Two classes now get DIFFERENT
severities for a missing SELECT:
  - `consumer_group` (baseline, e.g. the configured `account users`) — missing SELECT stays a
    BLOCKER; the pipeline never grants the baseline, so an unusable-Space failure here is real.
  - `declared_principals` (F2 AccessSpec principals) — missing SELECT becomes a non-blocking
    SUGGESTION ("será concedido pelo apply_access no deploy"); if apply_access itself then fails
    at deploy, that step fails loudly (existing behavior, unchanged here).
`non_grantable_principals` further splits the declared class: a declared principal apply_access
could NEVER grant (a workspace-LOCAL group — UC grants reject those, verified live) gets an advisory
that says so instead of a promise the pipeline can't keep.
"""
from __future__ import annotations

from typing import Callable, Iterable

_SELECT_OK = ("SELECT", "ALL_PRIVILEGES")
_GRANT01_CITATION = "Genie Promotion Handbook › Access › GRANT-01"


def _can_select(privilege_assignments: list[dict], principal: str) -> bool:
    for pa in privilege_assignments or []:
        if pa.get("principal") == principal and any(p in _SELECT_OK for p in pa.get("privileges", [])):
            return True
    return False


def _as_principal_list(consumer_group: "str | Iterable[str]") -> list[str]:
    """Normalize the `consumer_group` argument to an ordered, deduped list of principal names.
    Accepts a single group name (legacy call sites) or any iterable of names (F2: all declared
    AccessSpec principals) — never silently drops one just because a caller passed a bare string."""
    if isinstance(consumer_group, str):
        names = [consumer_group]
    else:
        names = list(consumer_group)
    seen: dict[str, None] = {}
    for n in names:
        if n:
            seen.setdefault(n, None)
    return list(seen)


def is_uc_grantable_group(resource_type: "str | None", display_name: str) -> bool:
    """Pure predicate (Additional scope, found live 2026-07-13): is a declared GROUP principal one
    UC grants will actually accept? UC grants only take ACCOUNT-level principals; a workspace-LOCAL
    group — the built-in `users`/`admins`, or any workspace-scoped custom group — is rejected
    outright ("Error: Could not find principal with name users").

    `resource_type` is the group's SCIM `meta.resourceType` (account groups report `"Group"`,
    workspace-local groups report `"WorkspaceGroup"`), or `None` when it couldn't be looked up/
    verified at all (e.g. a deleted group). Only in that unverifiable case do we fall back to
    excluding the well-known built-ins by NAME — never silently trust an unverifiable custom group
    just because its name looks fine.

    Only meaningful for GROUP principals — an individual user is always account-level, so callers
    should never run this against a declared user.
    """
    if resource_type is not None:
        return resource_type != "WorkspaceGroup"
    return display_name not in ("users", "admins")


def check_grants(
    space: dict,
    consumer_group: "str | Iterable[str]",
    get_effective_grants: Callable[[str], list[dict]],
    declared_principals: "str | Iterable[str] | None" = None,
    non_grantable_principals: "str | Iterable[str] | None" = None,
) -> list[dict]:
    """Return GRANT-01 findings for any (table, principal) pair missing SELECT.

    `consumer_group` — the BASELINE principal(s) (a single group name, legacy call sites, or an
    iterable). A missing grant here is a BLOCKER — unchanged, it's the hero "unusable Space"
    scenario, and the pipeline never grants the baseline for you.

    `declared_principals` (G9, optional) — every principal from the promotion's `AccessSpec`
    (`access_spec.AccessSpec.all_principals()`), checked SEPARATELY from the baseline: a missing
    grant here is a non-blocking SUGGESTION, because `apply_access` (scripts/apply_access.py) grants
    every declared principal at deploy — blocking pre-merge on something the pipeline is about to do
    would be contradictory. Any name also present in `consumer_group` is treated as baseline only
    (never double-reported under both severities).

    `non_grantable_principals` (G9, optional) — the subset of `declared_principals` apply_access
    could NEVER actually grant (a workspace-local group; see `is_uc_grantable_group`). These get an
    advisory that says so, instead of a promise the pipeline can't keep.

    `get_effective_grants(full_name)` -> list of {principal, privileges} (effective, i.e.
    including grants inherited from schema/catalog) — called ONCE per table regardless of how many
    principals are checked against it, so a multi-principal AccessSpec doesn't multiply UC calls.
    """
    baseline = _as_principal_list(consumer_group)
    baseline_set = set(baseline)
    declared = [p for p in _as_principal_list(declared_principals or []) if p not in baseline_set]
    non_grantable = set(_as_principal_list(non_grantable_principals or []))
    findings: list[dict] = []
    for t in (space.get("data_sources") or {}).get("tables", []) or []:
        fq = t.get("identifier", "")
        if not fq:
            continue
        try:
            grants = get_effective_grants(fq)
        except Exception as e:  # noqa: BLE001 — surface as a finding, don't crash the gate
            findings.append({"rule_id": "GRANT-01", "severity": "BLOCKER", "table": fq,
                             "message": f"não foi possível verificar grants de {fq}: {e}"})
            continue
        for principal in baseline:
            if not _can_select(grants, principal):
                findings.append({
                    "rule_id": "GRANT-01", "severity": "BLOCKER", "table": fq, "principal": principal,
                    "citation": _GRANT01_CITATION,
                    "message": f"o principal '{principal}' não tem SELECT em {fq} "
                               f"— o espaço seria inutilizável para ele (GRANT-01)",
                    "suggestion": f"Reaplicar GRANT SELECT em {fq} ao grupo de produção do espaço "
                                  f"(ou incluir '{principal}' nesse grupo).",
                })
        for principal in declared:
            if principal in non_grantable:
                findings.append({
                    "rule_id": "GRANT-01", "severity": "SUGGESTION", "table": fq, "principal": principal,
                    "citation": _GRANT01_CITATION,
                    "message": f"'{principal}' é um grupo local do workspace — não pode receber "
                               f"grant UC; use o equivalente de conta, ex.: account users",
                    "suggestion": "Trocar por um grupo/usuário de CONTA (não local do workspace) "
                                  "no picker de acesso.",
                })
            elif not _can_select(grants, principal):
                findings.append({
                    "rule_id": "GRANT-01", "severity": "SUGGESTION", "table": fq, "principal": principal,
                    "citation": _GRANT01_CITATION,
                    "message": f"o principal '{principal}' não tem SELECT em {fq} hoje "
                               f"— será concedido pelo pipeline (apply_access) no deploy",
                    "suggestion": "Nenhuma ação necessária agora: o apply_access concede este grant "
                                  "no deploy (se falhar, o passo de deploy falha de forma visível).",
                })
    return findings

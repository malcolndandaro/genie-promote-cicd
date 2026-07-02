"""Deterministic GRANT-01 check (S5, extended F2): does every declared principal have SELECT on
every data_source table of the (pre-rendered, prod) serialized_space?

This is the hero finding — a naming/schema linter can't see it; it needs UC state.
The check FINDS the gap deterministically; the agent then cites it as a grounded,
friendly GRANT-01 finding. Pure: the grants getter is injected, so it's unit-testable
without a workspace. The live shell injects effective-privileges lookups (SELECT can
cascade from a schema/catalog grant, so table-level grants alone would false-positive).

F2 extends the original single-consumer-group check to ALL principals declared in an `AccessSpec`
(every group/user from either the Space-permissions or the UC-grants half — see
`access_spec.AccessSpec.all_principals()`): a principal that can open a Space but can't read its
data is the exact "unusable Space" failure GRANT-01 exists to catch, so it's checked the same way
regardless of which half of the spec declared it. `consumer_group` accepts either a single string
(back-compat with the original single-group call sites) or an iterable of principal names.
"""
from __future__ import annotations

from typing import Callable, Iterable

_SELECT_OK = ("SELECT", "ALL_PRIVILEGES")


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


def check_grants(
    space: dict, consumer_group: "str | Iterable[str]", get_effective_grants: Callable[[str], list[dict]]
) -> list[dict]:
    """Return GRANT-01 findings for any (table, principal) pair missing SELECT.

    `consumer_group` — a single group name (legacy) or an iterable of ALL declared principals
    (F2: every group/user in the promotion's `AccessSpec`, from either permission system).
    `get_effective_grants(full_name)` -> list of {principal, privileges} (effective, i.e.
    including grants inherited from schema/catalog) — called ONCE per table regardless of how many
    principals are checked against it, so a multi-principal AccessSpec doesn't multiply UC calls.
    """
    principals = _as_principal_list(consumer_group)
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
        for principal in principals:
            if not _can_select(grants, principal):
                findings.append({
                    "rule_id": "GRANT-01", "severity": "BLOCKER", "table": fq, "principal": principal,
                    "citation": "Genie Promotion Handbook › Access › GRANT-01",
                    "message": f"o principal '{principal}' não tem SELECT em {fq} "
                               f"— o espaço seria inutilizável para ele (GRANT-01)",
                    "suggestion": f"Reaplicar GRANT SELECT em {fq} ao grupo de produção do espaço "
                                  f"(ou incluir '{principal}' nesse grupo).",
                })
    return findings

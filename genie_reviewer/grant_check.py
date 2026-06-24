"""Deterministic GRANT-01 check (S5): does the prod consumer group have SELECT on
every data_source table of the (pre-rendered, prod) serialized_space?

This is the hero finding — a naming/schema linter can't see it; it needs UC state.
The check FINDS the gap deterministically; the agent then cites it as a grounded,
friendly GRANT-01 finding. Pure: the grants getter is injected, so it's unit-testable
without a workspace. The live shell injects effective-privileges lookups (SELECT can
cascade from a schema/catalog grant, so table-level grants alone would false-positive).
"""
from __future__ import annotations

from typing import Callable

_SELECT_OK = ("SELECT", "ALL_PRIVILEGES")


def _can_select(privilege_assignments: list[dict], principal: str) -> bool:
    for pa in privilege_assignments or []:
        if pa.get("principal") == principal and any(p in _SELECT_OK for p in pa.get("privileges", [])):
            return True
    return False


def check_grants(
    space: dict, consumer_group: str, get_effective_grants: Callable[[str], list[dict]]
) -> list[dict]:
    """Return GRANT-01 findings for any data_source table the consumer group can't SELECT.

    `get_effective_grants(full_name)` -> list of {principal, privileges} (effective,
    i.e. including grants inherited from schema/catalog).
    """
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
        if not _can_select(grants, consumer_group):
            findings.append({"rule_id": "GRANT-01", "severity": "BLOCKER", "table": fq,
                             "citation": "Genie Promotion Handbook › Access › GRANT-01",
                             "message": f"o grupo '{consumer_group}' não tem SELECT em {fq} "
                                        f"— o espaço seria inutilizável (GRANT-01)",
                             "suggestion": f"Reaplicar GRANT SELECT em {fq} ao grupo de produção."})
    return findings

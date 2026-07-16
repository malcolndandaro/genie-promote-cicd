"""Pure deterministic AUDIENCE-01 validation.

Missing SELECT is guidance, not a gate. Invalid principals/tables are author-fixable blockers;
inability to inspect live grants is an operational failure so the pipeline can pause without
blaming the Space author.
"""
from __future__ import annotations

from collections.abc import Callable

from audience_spec import AudienceSpec

_SELECT_OK = {"SELECT", "ALL_PRIVILEGES"}
_CITATION = "CERC › Público do Space › AUDIENCE-01"


def _can_select(assignments: list[dict], principal: str) -> bool:
    return any(
        row.get("principal") == principal
        and bool(_SELECT_OK.intersection(row.get("privileges") or []))
        for row in assignments or []
    )


def check_audience(
    space: dict,
    spec: AudienceSpec,
    get_effective_grants: Callable[[str], list[dict]],
    *,
    principal_exists: Callable[[str, bool], bool] | None = None,
    table_exists: Callable[[str], bool] | None = None,
) -> list[dict]:
    findings: list[dict] = []
    valid_principals = []
    for principal in spec.principals:
        try:
            exists = True if principal_exists is None else principal_exists(
                principal.name, principal.is_group
            )
        except Exception as exc:  # directory/auth/timeout is operational, not bad author input
            findings.append({
                "rule_id": "AUDIENCE-01", "severity": "OPERATIONAL",
                "principal": principal.name, "citation": _CITATION,
                "message": f"não foi possível validar o principal '{principal.name}': {exc}",
                "suggestion": "A KIP deve restaurar a consulta ao diretório e repetir o Preflight.",
            })
            continue
        if not exists:
            findings.append({
                "rule_id": "AUDIENCE-01", "severity": "BLOCKER",
                "principal": principal.name, "citation": _CITATION,
                "message": f"o principal '{principal.name}' não existe ou não pode receber CAN_RUN",
                "suggestion": "Escolha novamente um usuário ou grupo disponível no diretório.",
            })
            continue
        valid_principals.append(principal)

    for table in (space.get("data_sources") or {}).get("tables", []) or []:
        full_name = str(table.get("identifier") or "").strip()
        if len(full_name.split(".")) != 3:
            findings.append({
                "rule_id": "AUDIENCE-01", "severity": "BLOCKER", "table": full_name,
                "citation": _CITATION, "message": f"referência de tabela inválida: {full_name or '(vazia)'}",
                "suggestion": "Use uma tabela existente no formato catálogo.schema.tabela.",
            })
            continue
        try:
            exists = True if table_exists is None else table_exists(full_name)
        except Exception as exc:
            findings.append({
                "rule_id": "AUDIENCE-01", "severity": "OPERATIONAL", "table": full_name,
                "citation": _CITATION,
                "message": f"não foi possível validar a tabela {full_name}: {exc}",
                "suggestion": "A KIP deve restaurar a consulta ao catálogo e repetir o Preflight.",
            })
            continue
        if not exists:
            findings.append({
                "rule_id": "AUDIENCE-01", "severity": "BLOCKER", "table": full_name,
                "citation": _CITATION, "message": f"a tabela de produção {full_name} não existe",
                "suggestion": "Corrija o de-para ou publique a tabela antes da promoção.",
            })
            continue
        try:
            grants = get_effective_grants(full_name)
        except Exception as exc:
            findings.append({
                "rule_id": "AUDIENCE-01", "severity": "OPERATIONAL", "table": full_name,
                "citation": _CITATION,
                "message": f"não foi possível inspecionar grants efetivos de {full_name}: {exc}",
                "suggestion": "A KIP deve restaurar a inspeção de grants e repetir o Preflight.",
            })
            continue
        for principal in valid_principals:
            if not _can_select(grants, principal.name):
                findings.append({
                    "rule_id": "AUDIENCE-01", "severity": "SUGGESTION",
                    "table": full_name, "principal": principal.name, "citation": _CITATION,
                    "message": (
                        f"'{principal.name}' ainda não tem SELECT em {full_name}; "
                        "a promoção pode seguir, mas o acesso aos dados deve ser solicitado pela fila Terraform da CERC"
                    ),
                    "suggestion": "Abra ou acompanhe a solicitação de acesso no processo Terraform da CERC.",
                })
    return findings

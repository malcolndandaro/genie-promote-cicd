"""/genie-fix core (S6) — pure, unit-testable.

Asks the LLM for a COMPLETE corrected serialized_space that resolves the auto-fixable
findings, then validates structure before the bot pushes (mirrors bimbo fix_pr.py, but
the artifact is a Genie serialized_space JSON, not a code file).

Auto-fixable: instruction/SQL/semantic edits (EVAL-02 vague instructions, SQL-01
conventions, an ENV catalog ref inside SQL). NOT auto-fixable by a JSON rewrite:
GRANT-01 (re-apply the UC grant at deploy) and EVAL-01 (authoring benchmark Q→SQL is
human-confirmed) — these are reported, never silently invented.
"""
from __future__ import annotations

import json
import re

import review_core  # sibling pure module (no heavy deps) — reuse its balanced-JSON scanner

_FENCE = re.compile(r"```[a-zA-Z0-9_]*\n(?P<code>.*?)```", re.S)
# Auto-fixable = instruction/SQL TEXT rewrites that don't touch tables/columns/grants/env.
# Everything else is owned elsewhere: ENV-01/ENV-02 by the deterministic pre-render
# (dev_->prod_ + warehouse, ADR-0003 — re-pointing an identifier is NOT the agent's job);
# GRANT-01/GRANT-02 by UC grants at deploy; EVAL-01 by benchmark authoring (human); PII-01
# by masking/column changes (human-confirmed, BCB-538). Auto-fix only EVAL-02, SQL-01, PII-02.
_NON_AUTOFIX = {"GRANT-01", "GRANT-02", "EVAL-01", "ENV-01", "ENV-02", "PII-01"}
_WRITE_PERMS = ("write", "maintain", "admin")

_FIX_SYSTEM = (
    "Você é o Genie Reviewer em MODO CORREÇÃO. Recebe um serialized_space de um Genie Space "
    "e uma lista de hallazgos. Devolva o serialized_space COMPLETO e corrigido que: "
    "(1) resolve os hallazgos seguindo o Genie Promotion Handbook; "
    "(2) NÃO remove nem adiciona tabelas em data_sources e NÃO altera os identifiers; "
    "(3) preserva a estrutura e o comportamento do restante; "
    "(4) torna instruções vagas específicas (cite as tabelas Diamond) e ajusta SQL às "
    "convenções (MAIÚSCULAS, sem SELECT *, listar colunas). "
    "Não explique nada: devolva SOMENTE o JSON corrigido dentro de um único bloco ```...```."
)


def is_authorized(permission: str, branch_is_protected: bool) -> tuple[bool, str]:
    """Only write/maintain/admin collaborators, never on a protected branch (mirrors ADR guardrail)."""
    if branch_is_protected:
        return False, "a rama de destino está protegida; o bot não escreve nela."
    if permission not in _WRITE_PERMS:
        return False, f"permissão insuficiente ('{permission}'); requer write/maintain/admin."
    return True, ""


def fixable_findings(findings: list[dict]) -> list[dict]:
    return [f for f in (findings or []) if f.get("rule_id") not in _NON_AUTOFIX]


def build_fix_prompt(space: dict, findings: list[dict]) -> tuple[str, str]:
    fixable = fixable_findings(findings)
    issues = "\n".join(
        f"- {f.get('rule_id')} [{f.get('severity')}]: {f.get('message')}" for f in fixable
    ) or "(nenhum hallazgo auto-corrigível)"
    user = (
        f"HALLAZGOS A CORRIGIR:\n{issues}\n\n"
        f"SERIALIZED_SPACE ATUAL:\n```json\n{json.dumps(space, ensure_ascii=False, indent=2)}\n```\n\n"
        "Devolva o serialized_space COMPLETO corrigido em um único bloco de código JSON."
    )
    return _FIX_SYSTEM, user


def extract_json(model_output: str) -> dict | None:
    """Pull the corrected serialized_space out of the model output.

    Tries every fenced block (not just the first — the model may emit a prose/text
    block first), then the whole string, then a balanced-object scan. Returns the first
    that parses to a dict.
    """
    if not isinstance(model_output, str):
        return None
    candidates = [m.group("code") for m in _FENCE.finditer(model_output)]
    candidates.append(model_output)
    bal = review_core._first_balanced_object(model_output)
    if bal:
        candidates.append(bal)
    for c in candidates:
        try:
            obj = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _table_ids(space: dict):
    """Sorted data_source identifiers, or None if the structure is malformed (never raises)."""
    ds = space.get("data_sources") if isinstance(space, dict) else None
    tables = ds.get("tables") if isinstance(ds, dict) else None
    if not isinstance(tables, list):
        return None
    ids = []
    for t in tables:
        if not isinstance(t, dict):
            return None
        ids.append(t.get("identifier", ""))
    return sorted(ids)


def _columns(space: dict) -> dict:
    """{identifier: sorted(column_names)} — the columns the space exposes per table."""
    out: dict = {}
    for t in (space.get("data_sources") or {}).get("tables", []) or []:
        if not isinstance(t, dict):
            continue
        cols = [c.get("column_name") for c in (t.get("column_configs") or []) if isinstance(c, dict)]
        out[t.get("identifier", "")] = sorted(c for c in cols if c)
    return out


def validate_fixed_space(original: dict, fixed: dict | None) -> tuple[bool, str]:
    """The fix must stay a structurally-valid serialized_space exposing the SAME tables
    and columns, with no injected top-level keys — the bot never pushes a fix that widens
    what the space exposes (column injection, env/warehouse swap, extra keys). Never raises.
    """
    if not isinstance(fixed, dict):
        return False, "a saída não é um objeto JSON"
    if not isinstance(fixed.get("data_sources"), dict) or not isinstance(fixed.get("instructions"), dict):
        return False, "estrutura faltando/ inválida (data_sources/instructions devem ser objetos)"
    extra = set(fixed) - set(original)
    if extra:
        return False, f"chaves de topo novas (proibido): {sorted(extra)}"
    o_ids, f_ids = _table_ids(original), _table_ids(fixed)
    if f_ids is None:
        return False, "data_sources/tables malformado"
    if o_ids != f_ids:
        return False, "o conjunto de tabelas/identifiers mudou (proibido)"
    if _columns(original) != _columns(fixed):
        return False, "as colunas expostas por alguma tabela mudaram (proibido)"
    return True, ""

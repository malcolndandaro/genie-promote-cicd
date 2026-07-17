"""Unit tests for /genie-fix core (S6). Pure — no LLM/Databricks."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import fix_core  # noqa: E402

SPACE = {
    "data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
        {"identifier": "prod_recebiveis.diamond.dim_arranjo"},
    ]},
    "instructions": {"text_instructions": [{"content": ["Responda qualquer coisa."]}],
                     "example_question_sqls": []},
}
FINDINGS = [
    {"rule_id": "AUDIENCE-01", "severity": "SUGGESTION", "message": "sem SELECT"},
    {"rule_id": "EVAL-01", "severity": "BLOCKER", "message": "poucos benchmarks"},
    {"rule_id": "EVAL-02", "severity": "SUGGESTION", "message": "instrução vaga"},
    {"rule_id": "SQL-01", "severity": "STYLE", "message": "SELECT *"},
]


def test_fixable_excludes_audience_and_eval01():
    ids = {f["rule_id"] for f in fix_core.fixable_findings(FINDINGS)}
    assert ids == {"EVAL-02", "SQL-01"}


def test_build_fix_prompt_has_fixable_and_space():
    system, user = fix_core.build_fix_prompt(SPACE, FINDINGS)
    assert "EVAL-02" in user and "SQL-01" in user
    assert "AUDIENCE-01" not in user
    assert "prod_recebiveis.diamond.fato_recebiveis" in user
    assert "CORREÇÃO" in system


def test_extract_json_fenced_and_bare():
    obj = {"data_sources": {"tables": []}, "instructions": {}}
    assert fix_core.extract_json("```json\n" + json.dumps(obj) + "\n```") == obj
    assert fix_core.extract_json(json.dumps(obj)) == obj
    assert fix_core.extract_json("not json") is None


def test_validate_ok_when_tables_preserved():
    fixed = {"data_sources": SPACE["data_sources"], "instructions": {"text_instructions": [{"content": ["específico"]}]}}
    ok, _ = fix_core.validate_fixed_space(SPACE, fixed)
    assert ok


def test_validate_rejects_dropped_table():
    fixed = {"data_sources": {"tables": [{"identifier": "prod_recebiveis.diamond.fato_recebiveis"}]},
             "instructions": {}}
    ok, reason = fix_core.validate_fixed_space(SPACE, fixed)
    assert not ok and "tabelas" in reason


def test_validate_rejects_missing_structure():
    ok, _ = fix_core.validate_fixed_space(SPACE, {"data_sources": {"tables": []}})
    assert not ok


def test_authorization_guardrails():
    assert fix_core.is_authorized("admin", False)[0] is True
    assert fix_core.is_authorized("read", False)[0] is False
    assert fix_core.is_authorized("admin", True)[0] is False  # protected branch


# --- S6 review hardening: ENV/PII exclusion, no-crash guards, injection rejection ---
def test_fixable_excludes_env_pii_audience():
    fs = [{"rule_id": r, "severity": "x", "message": r}
          for r in ["ENV-01", "ENV-02", "PII-01", "AUDIENCE-01", "EVAL-02", "SQL-01", "PII-02"]]
    assert {f["rule_id"] for f in fix_core.fixable_findings(fs)} == {"EVAL-02", "SQL-01", "PII-02"}


def test_validate_never_raises_on_malformed():
    for bad in [{"data_sources": "x", "instructions": {}},
                {"data_sources": {"tables": "oops"}, "instructions": {}},
                {"data_sources": {"tables": ["str"]}, "instructions": {}}]:
        ok, _ = fix_core.validate_fixed_space(SPACE, bad)
        assert ok is False  # returns, never throws


def test_validate_rejects_injected_top_level_key():
    fixed = dict(SPACE)
    fixed["evil_extra"] = True
    ok, reason = fix_core.validate_fixed_space(SPACE, fixed)
    assert not ok and "novas" in reason


def test_validate_rejects_added_column():
    base = {"data_sources": {"tables": [{"identifier": "c.s.t", "column_configs": [{"column_name": "a"}]}]},
            "instructions": {}}
    fixed = {"data_sources": {"tables": [{"identifier": "c.s.t",
             "column_configs": [{"column_name": "a"}, {"column_name": "cpf"}]}]}, "instructions": {}}
    ok, reason = fix_core.validate_fixed_space(base, fixed)
    assert not ok and "colunas" in reason


def test_extract_json_skips_nonjson_first_fence():
    obj = {"data_sources": {"tables": []}, "instructions": {}}
    out = "Aqui está:\n```text\nnão é json\n```\n```json\n" + json.dumps(obj) + "\n```"
    assert fix_core.extract_json(out) == obj

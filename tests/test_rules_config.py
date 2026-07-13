"""Offline tests for genie_reviewer/rules_config.py (G2) — pure, no store/LLM/network.

Covers exactly the acceptance-criteria-bearing cases: (1) no overrides = byte-identical to
handbook_rules.RULES (the "safe fallback" guarantee), (2) a disabled rule is dropped, (3) a
severity override is applied, (4) EVAL-01's params (min_benchmarks) override is respected, and
(5) a custom rule is appended.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "genie_reviewer"))

import handbook_rules  # noqa: E402
import review_core  # noqa: E402
import rules_config as rc  # noqa: E402


# --- effective_rules: the no-store-identical guarantee -------------------------------------------


def test_no_overrides_is_byte_identical_to_hardcoded_rules():
    assert rc.effective_rules(None) == handbook_rules.RULES
    assert rc.effective_rules([]) == handbook_rules.RULES


def test_an_override_for_an_unrelated_rule_leaves_everything_else_untouched():
    out = rc.effective_rules([{"rule_id": "SQL-01", "enabled": True, "severity": None, "params": None}])
    assert out == handbook_rules.RULES  # SQL-01's override set nothing that differs from default


def test_disabled_rule_is_dropped_from_the_effective_set():
    out = rc.effective_rules([{"rule_id": "SQL-01", "enabled": False}])
    ids = {r["rule_id"] for r in out}
    assert "SQL-01" not in ids
    assert len(out) == len(handbook_rules.RULES) - 1


def test_severity_override_replaces_severity_hint():
    out = rc.effective_rules([{"rule_id": "ENV-02", "enabled": True, "severity": "BLOCKER"}])
    env02 = next(r for r in out if r["rule_id"] == "ENV-02")
    assert env02["severity_hint"] == "BLOCKER"  # default hint is SUGGESTION


def test_params_override_is_carried_onto_the_effective_rule():
    out = rc.effective_rules([{"rule_id": "EVAL-01", "enabled": True, "params": {"min_benchmarks": 5}}])
    ev = next(r for r in out if r["rule_id"] == "EVAL-01")
    assert ev["params"] == {"min_benchmarks": 5}


def test_custom_rule_is_appended():
    out = rc.effective_rules([{"rule_id": "CUSTOM-01", "is_custom": True, "enabled": True,
                               "severity": "STYLE", "content": "nunca use SELECT *",
                               "citation": "Padrão interno"}])
    assert len(out) == len(handbook_rules.RULES) + 1
    custom = next(r for r in out if r["rule_id"] == "CUSTOM-01")
    assert custom["severity_hint"] == "STYLE" and custom["content"] == "nunca use SELECT *"


def test_disabled_custom_rule_is_not_appended():
    out = rc.effective_rules([{"rule_id": "CUSTOM-01", "is_custom": True, "enabled": False,
                               "severity": "STYLE", "content": "c", "citation": "cit"}])
    assert out == handbook_rules.RULES


def test_a_hardcoded_rule_untouched_by_any_override_is_unchanged_object_content():
    out = rc.effective_rules([{"rule_id": "SQL-01", "enabled": False}])
    env01 = next(r for r in out if r["rule_id"] == "ENV-01")
    hardcoded_env01 = next(r for r in handbook_rules.RULES if r["rule_id"] == "ENV-01")
    assert env01 == hardcoded_env01


# --- eval01_config: the EVAL-01 deterministic backstop's (min_benchmark, severity) ---------------


def test_eval01_config_defaults_when_no_override():
    n, sev = rc.eval01_config(None)
    assert (n, sev) == (2, "BLOCKER")


def test_eval01_config_reads_min_benchmarks_param():
    n, sev = rc.eval01_config([{"rule_id": "EVAL-01", "enabled": True, "params": {"min_benchmarks": 5}}])
    assert n == 5 and sev == "BLOCKER"


def test_eval01_config_reads_severity_override():
    n, sev = rc.eval01_config([{"rule_id": "EVAL-01", "enabled": True, "severity": "SUGGESTION"}])
    assert n == 2 and sev == "SUGGESTION"


def test_eval01_config_disabled_collapses_min_benchmark_to_zero():
    # never blocks regardless of actual benchmark count (n_benchmark is never negative) — an
    # explicit, audited admin choice (the override write itself is audited), not a silent bypass.
    n, sev = rc.eval01_config([{"rule_id": "EVAL-01", "enabled": False}])
    assert n == 0


def test_eval01_config_ignores_malformed_min_benchmarks():
    n, _ = rc.eval01_config([{"rule_id": "EVAL-01", "enabled": True, "params": {"min_benchmarks": "not-a-number"}}])
    assert n == 2  # falls back to the default rather than raising


def test_eval01_config_ignores_negative_min_benchmarks():
    n, _ = rc.eval01_config([{"rule_id": "EVAL-01", "enabled": True, "params": {"min_benchmarks": -1}}])
    assert n == 2


# --- end-to-end: a custom rule reaches the reviewer prompt AND survives to findings when violated -
# (G2 acceptance criterion: "regra custom criada aparece no prompt do reviewer e nos findings
# quando violada"). The LLM call itself is out of scope here (no network) — this proves the two
# halves that make it possible: the rule text is grounded in the prompt, and an LLM finding citing
# that custom rule_id is NOT dropped by the parse/finalize pipeline (which never filters findings
# against a fixed rule_id allowlist — it only special-cases the 2 deterministic backstops).


def test_custom_rule_text_is_grounded_in_the_review_prompt():
    overrides = [{"rule_id": "SQL-02", "is_custom": True, "enabled": True, "severity": "STYLE",
                  "content": "nunca use SELECT * em SQL de exemplo", "citation": "Padrão interno de SQL"}]
    effective = rc.effective_rules(overrides)
    ctx = review_core.build_space_context({"data_sources": {"tables": []}, "instructions": {}})
    _, user = review_core.build_review_prompt(ctx, effective)
    assert "SQL-02" in user and "nunca use SELECT * em SQL de exemplo" in user


def test_a_finding_citing_a_custom_rule_survives_to_the_final_findings():
    llm_finding = {"severity": "STYLE", "rule_id": "SQL-02", "citation": "Padrão interno de SQL",
                  "message": "usa SELECT * na query de exemplo", "suggestion": None}
    out = review_core.finalize_findings([llm_finding], [], n_benchmark=3)
    assert any(f["rule_id"] == "SQL-02" for f in out)

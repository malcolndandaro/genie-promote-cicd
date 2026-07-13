"""Unit tests for the Genie Reviewer pure cores (S5). No Databricks / no LLM."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import handbook_rules  # noqa: E402
import review_core as rc  # noqa: E402

SPACE = {
    "version": 2,
    "data_sources": {
        "tables": [
            {"identifier": "prod_recebiveis.diamond.fato_recebiveis",
             "column_configs": [{"column_name": "valor"}, {"column_name": "status"}]},
            {"identifier": "dev_recebiveis.diamond.dim_cedente", "column_configs": []},
        ]
    },
    "instructions": {
        "text_instructions": [{"content": ["Responda qualquer coisa sobre recebíveis."]}],
        # "Example queries" — a SEPARATE validation signal (not the benchmark count).
        "example_question_sqls": [
            {"question": ["Volume?"], "sql": ["SELECT * FROM dev_recebiveis.diamond.fato_recebiveis"]}
        ],
        "join_specs": [
            {"left": {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
             "right": {"identifier": "prod_recebiveis.diamond.dim_cedente"}}
        ],
    },
    # The "Benchmarks"/eval tab (top-level) — THIS is what EVAL-01 counts.
    "benchmarks": {
        "questions": [
            {"question": ["Top 5 cedentes?"], "answer": [{"format": "SQL", "content": ["SELECT 1"]}]},
            {"question": ["Volume 3 meses?"], "answer": [{"format": "SQL", "content": ["SELECT 2"]}]},
        ]
    },
}


def test_context_extracts_surface():
    ctx = rc.build_space_context(SPACE)
    assert len(ctx["tables"]) == 2
    assert ctx["n_benchmark"] == 2                  # from benchmarks.questions, NOT example queries
    assert len(ctx["example_sqls"]) == 1            # example queries are a separate signal
    assert "valor" in ctx["tables"][0]["columns"]
    assert len(ctx["joins"]) == 1


def test_benchmark_count_uses_benchmarks_not_example_queries():
    # Example queries are NOT benchmarks: a space with only example_question_sqls counts 0 benchmarks.
    only_examples = {"instructions": {"example_question_sqls": [
        {"question": ["q1"], "sql": ["SELECT 1"]}, {"question": ["q2"], "sql": ["SELECT 2"]}]}}
    assert rc.build_space_context(only_examples)["n_benchmark"] == 0
    # And a space whose Q->SQL pairs live under `benchmarks` IS counted (the bug Pedro hit).
    only_bench = {"benchmarks": {"questions": [
        {"question": ["q1"], "answer": [{"format": "SQL", "content": ["SELECT 1"]}]},
        {"question": ["q2"], "answer": [{"format": "SQL", "content": ["SELECT 2"]}]}]}}
    ctx = rc.build_space_context(only_bench)
    assert ctx["n_benchmark"] == 2 and ctx["benchmark_questions"] == ["q1", "q2"]


def test_prompt_includes_rules_and_space():
    ctx = rc.build_space_context(SPACE)
    system, user = rc.build_review_prompt(ctx, handbook_rules.RULES)
    assert "ENV-01" in user and "GRANT-01" in user
    assert "dev_recebiveis.diamond.dim_cedente" in user
    assert "PORTUGU" in system.upper()


def test_prompt_includes_grant_findings():
    ctx = rc.build_space_context(SPACE)
    _, user = rc.build_review_prompt(
        ctx, handbook_rules.RULES,
        grant_findings=[{"message": "grupo de produção sem SELECT em prod_recebiveis.diamond.dim_arranjo"}],
    )
    assert "dim_arranjo" in user


def test_prompt_shows_grant_finding_severity_so_the_llm_never_narrates_an_advisory_as_a_blocker():
    """G9: a GRANT-01 finding's severity is already decided deterministically (BLOCKER for the
    baseline group, SUGGESTION for a declared AccessSpec principal) — the prompt must show that
    severity per finding, not a blanket 'treat as BLOCKER' instruction that would mislead the LLM's
    narrative for a merely-advisory finding."""
    ctx = rc.build_space_context(SPACE)
    _, user = rc.build_review_prompt(
        ctx, handbook_rules.RULES,
        grant_findings=[
            {"severity": "BLOCKER", "message": "grupo baseline sem SELECT"},
            {"severity": "SUGGESTION", "message": "ana@x.com sem SELECT — será concedido no deploy"},
        ],
    )
    assert "[BLOCKER] grupo baseline sem SELECT" in user
    assert "[SUGGESTION] ana@x.com sem SELECT" in user
    assert "considere como BLOCKER" not in user


def test_parse_review_tolerant_fenced():
    raw = '```json\n{"summary":"s","findings":[{"severity":"BLOCKER","rule_id":"ENV-01",' \
          '"citation":"c","message":"m","suggestion":null}]}\n```'
    out = rc.parse_review(raw)
    assert out["summary"] == "s" and len(out["findings"]) == 1
    assert out["findings"][0]["rule_id"] == "ENV-01"


def test_parse_findings_drops_invalid():
    raw = {"findings": [
        {"severity": "NOPE", "rule_id": "X", "citation": "c", "message": "m"},
        {"severity": "BLOCKER", "rule_id": "GRANT-01", "citation": "c", "message": "m"},
    ]}
    fs = rc.parse_findings(raw)
    assert len(fs) == 1 and fs[0]["rule_id"] == "GRANT-01"


def test_gate_blocker_fails():
    d = rc.decide_gate([{"severity": "BLOCKER", "rule_id": "ENV-01"}])
    assert d["conclusion"] == "failure" and d["blocker_count"] == 1


def test_gate_suggestion_only_neutral():
    d = rc.decide_gate([{"severity": "SUGGESTION", "rule_id": "EVAL-02"}])
    assert d["conclusion"] == "neutral"


def test_gate_clean_success():
    assert rc.decide_gate([])["conclusion"] == "success"


def test_buried_catalog_ref_survives_truncation():
    # real serialized_space pads SQL lines with ~150 spaces; the dev ref sits past 300.
    padded_sql = "SELECT *" + " " * 400 + "FROM sbx_recebiveis.diamond.fato_recebiveis"
    space = {"data_sources": {"tables": []},
             "instructions": {"example_question_sqls": [{"question": ["q"], "sql": [padded_sql]}]}}
    ctx = rc.build_space_context(space)
    _, user = rc.build_review_prompt(ctx, handbook_rules.RULES)
    assert "sbx_recebiveis.diamond.fato_recebiveis" in user  # visible after whitespace-collapse


def test_finalize_keeps_deterministic_grant_even_if_llm_silent():
    det = [{"severity": "BLOCKER", "rule_id": "GRANT-01", "citation": "c", "message": "m"}]
    out = rc.finalize_findings([], det, n_benchmark=3)
    assert any(f["rule_id"] == "GRANT-01" and f["severity"] == "BLOCKER" for f in out)


def test_finalize_adds_eval01_backstop_when_too_few_benchmarks():
    out = rc.finalize_findings([], [], n_benchmark=1)
    assert any(f["rule_id"] == "EVAL-01" and f["severity"] == "BLOCKER" for f in out)


def test_finalize_no_eval01_at_two_benchmarks():
    # Prod threshold is >= 2 benchmark Q→SQL, so a 2-question space is clean (enables the
    # end-to-end steward-approval path on the dev space).
    out = rc.finalize_findings([], [], n_benchmark=2)
    assert not any(f["rule_id"] == "EVAL-01" for f in out)


def test_finalize_eval01_severity_defaults_to_blocker():
    # Backward compatibility: a caller that doesn't pass eval01_severity (every pre-G2 call site)
    # sees the exact same BLOCKER severity as before this parameter existed.
    out = rc.finalize_findings([], [], n_benchmark=0)
    ev = next(f for f in out if f["rule_id"] == "EVAL-01")
    assert ev["severity"] == "BLOCKER"


def test_finalize_eval01_severity_is_configurable():
    # G2: an admin override (via rules_config.eval01_config) can downgrade the backstop's severity.
    out = rc.finalize_findings([], [], n_benchmark=0, eval01_severity="SUGGESTION")
    ev = next(f for f in out if f["rule_id"] == "EVAL-01")
    assert ev["severity"] == "SUGGESTION"


def test_finalize_min_benchmark_is_configurable():
    # G2: an admin-raised threshold (e.g. min_benchmarks=5) fires the backstop even at 3 benchmarks.
    out = rc.finalize_findings([], [], n_benchmark=3, min_benchmark=5)
    assert any(f["rule_id"] == "EVAL-01" for f in out)


def test_finalize_dedups_llm_against_deterministic_owner():
    det = [{"severity": "BLOCKER", "rule_id": "GRANT-01", "citation": "c", "message": "det"}]
    llm = [{"severity": "SUGGESTION", "rule_id": "GRANT-01", "citation": "c", "message": "llm-soft"},
           {"severity": "STYLE", "rule_id": "SQL-01", "citation": "c", "message": "keep"}]
    out = rc.finalize_findings(llm, det, n_benchmark=3)
    grant = [f for f in out if f["rule_id"] == "GRANT-01"]
    assert len(grant) == 1 and grant[0]["message"] == "det"  # deterministic wins
    assert any(f["rule_id"] == "SQL-01" for f in out)        # LLM semantic kept


def test_handbook_has_nine_rules_with_required_fields():
    assert len(handbook_rules.RULES) == 9
    for r in handbook_rules.RULES:
        assert r["rule_id"] and r["severity_hint"] in rc.SEVERITIES and r["citation"] and r["content"]

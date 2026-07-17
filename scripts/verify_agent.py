#!/usr/bin/env python3
"""Local verification of the Genie Reviewer (S5) — no mlflow/SDK needed.

Builds a deliberately-bad PROD serialized_space + a deterministic AUDIENCE-01 finding,
assembles the handbook-grounded prompt, calls the Claude serving endpoint directly,
and prints the parsed PT findings + severity gate. Proves the agent's differentiator
(catches Audience + EVAL violations a naming linter cannot see) without deploying a
Model Serving endpoint.

Usage: python3 scripts/verify_agent.py [profile] [llm-endpoint]
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import handbook_rules  # noqa: E402
import review_core  # noqa: E402

PROFILE = sys.argv[1] if len(sys.argv) > 1 else "databricks-dev"
LLM = sys.argv[2] if len(sys.argv) > 2 else "databricks-claude-opus-4-8"

BAD_PROD_SPACE = {
    "version": 2,
    "data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis",
         "column_configs": [{"column_name": "valor"}, {"column_name": "status"}]},
        {"identifier": "prod_recebiveis.diamond.dim_arranjo",
         "column_configs": [{"column_name": "arranjo"}]},
    ]},
    "instructions": {
        "text_instructions": [{"content": ["Responda qualquer coisa sobre recebíveis."]}],
        "example_question_sqls": [
            {"question": ["Qual o volume?"],
             "sql": ["SELECT * FROM prod_recebiveis.diamond.fato_recebiveis"]}
        ],
        "join_specs": [],
    },
}
# Deterministic audience finding (would come from the content preflight):
DETERMINISTIC_FINDINGS = [{
    "rule_id": "AUDIENCE-01", "severity": "BLOCKER",
    "citation": "CERC › Público do Space › AUDIENCE-01",
    "message": "o grupo 'grp_recebiveis_consumer' não tem SELECT em "
               "prod_recebiveis.diamond.dim_arranjo",
    "suggestion": "Corrigir o principal ou a tabela antes de promover.",
}]


def call_claude(system: str, user: str) -> str:
    req = {"messages": [{"role": "system", "content": system},
                        {"role": "user", "content": user}], "max_tokens": 1800}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(req, fh)
        path = fh.name
    out = subprocess.check_output(
        ["databricks", "serving-endpoints", "query", LLM, "--json", f"@{path}", "-p", PROFILE, "-o", "json"])
    resp = json.loads(out)
    # foundation-model chat shape
    if isinstance(resp, dict) and resp.get("choices"):
        return resp["choices"][0]["message"]["content"]
    return json.dumps(resp)


def main() -> int:
    ctx = review_core.build_space_context(BAD_PROD_SPACE)
    system, user = review_core.build_review_prompt(ctx, handbook_rules.RULES, DETERMINISTIC_FINDINGS)
    review = review_core.parse_review(call_claude(system, user))
    review["findings"] = review_core.finalize_findings(
        review["findings"], DETERMINISTIC_FINDINGS, ctx["n_benchmark"])
    gate = review_core.decide_gate(review["findings"])
    print("=== Genie Reviewer — findings ===")
    for f in review["findings"]:
        print(f"  [{f['severity']}] {f['rule_id']}: {f['message']}")
    print(f"\nsummary: {review['summary']}")
    print(f"gate: {gate['conclusion']} (blockers={gate['blocker_count']}) — {gate['summary']}")
    rule_ids = {f["rule_id"] for f in review["findings"]}
    ok = gate["conclusion"] == "failure" and "AUDIENCE-01" in rule_ids
    print(f"\nVERIFY: {'PASS' if ok else 'CHECK'} (rules cited: {sorted(rule_ids)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Local verification of /genie-fix (S6): review a bad space, fix it via Claude,
validate the fix preserves the tables/structure, and re-review to confirm the
auto-fixable findings cleared. No mlflow/SDK needed (CLI serving query).
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import fix_core  # noqa: E402
import handbook_rules  # noqa: E402
import review_core  # noqa: E402

PROFILE = sys.argv[1] if len(sys.argv) > 1 else "databricks-dev"
LLM = "databricks-claude-opus-4-8"

BAD = {
    "version": 2,
    "data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis",
         "column_configs": [{"column_name": "valor"}, {"column_name": "arranjo"}]},
        {"identifier": "prod_recebiveis.diamond.dim_arranjo",
         "column_configs": [{"column_name": "arranjo"}, {"column_name": "bandeira"}]},
    ]},
    "instructions": {
        "text_instructions": [{"content": ["Responda qualquer coisa sobre recebíveis."]}],
        "example_question_sqls": [
            {"question": ["Volume por bandeira?"],
             "sql": ["select * from prod_recebiveis.diamond.fato_recebiveis f join prod_recebiveis.diamond.dim_arranjo a on f.arranjo = a.arranjo"]},
            {"question": ["Top arranjos?"], "sql": ["select * from prod_recebiveis.diamond.dim_arranjo"]},
            {"question": ["Total?"], "sql": ["select count(*) from prod_recebiveis.diamond.fato_recebiveis"]},
        ],
    },
}


def claude(system: str, user: str, max_tokens: int = 4000) -> str:
    req = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
           "max_tokens": max_tokens}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(req, fh)
        path = fh.name
    out = subprocess.check_output(
        ["databricks", "serving-endpoints", "query", LLM, "--json", f"@{path}", "-p", PROFILE, "-o", "json"])
    return json.loads(out)["choices"][0]["message"]["content"]


def review(space: dict) -> list[dict]:
    ctx = review_core.build_space_context(space)
    system, user = review_core.build_review_prompt(ctx, handbook_rules.RULES, [])
    r = review_core.parse_review(claude(system, user))
    return review_core.finalize_findings(r["findings"], [], ctx["n_benchmark"])


def main() -> int:
    before = review(BAD)
    before_ids = {f["rule_id"] for f in before}
    print("BEFORE fix, findings:", sorted(before_ids))
    system, user = fix_core.build_fix_prompt(BAD, before)
    fixed = fix_core.extract_json(claude(system, user))
    ok, reason = fix_core.validate_fixed_space(BAD, fixed)
    print("fix valid:", ok, reason)
    if not ok:
        return 1
    after = review(fixed)
    after_ids = {f["rule_id"] for f in after}
    after_blockers = {f["rule_id"] for f in after if f.get("severity") == "BLOCKER"}
    print("AFTER fix, findings:", sorted(after_ids))
    print("instructions after:", fixed["instructions"]["text_instructions"][0]["content"])
    print("sql[0] after:", fixed["instructions"]["example_question_sqls"][0]["sql"])
    tables_ok = fix_core._table_ids(BAD) == fix_core._table_ids(fixed)
    # honest bar: valid fix, tables preserved, findings reduced, no NEW blocker introduced
    ok2 = ok and tables_ok and len(after) < len(before) and not after_blockers
    print(f"\nVERIFY: {'PASS' if ok2 else 'CHECK'} "
          f"(tables_preserved={tables_ok}, {len(before)}->{len(after)} findings, "
          f"cleared={sorted(before_ids - after_ids)}, blockers_after={sorted(after_blockers)})")
    return 0 if ok2 else 1


if __name__ == "__main__":
    raise SystemExit(main())

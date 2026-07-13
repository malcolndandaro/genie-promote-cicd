"""rules_config — G2: the pure merge between the hardcoded handbook rules (`handbook_rules.RULES`)
and admin-configured overrides (rows from `app/rules_store.py`, passed in here as plain dicts so
this module has NO Lakebase/store dependency — as pure/offline-testable as `review_core.py`, which
is exactly what consumes its output).

`effective_rules(None)` / `effective_rules([])` returns rules equal in content to
`handbook_rules.RULES` — the "no store configured / no overrides" guarantee G2's acceptance
criteria requires: offline CI and a fresh install behave EXACTLY like before this slice shipped.

Two of the 9 rules are enforced OUTSIDE the LLM-grounded prompt, as deterministic backstops the LLM
can't soften (`review_core.finalize_findings`'s synthetic EVAL-01 finding; `grant_check.py`'s
GRANT-01 findings) — so overriding them can't just mean "edit the rules_block text" the way the
other 7 work. `eval01_config()` is the second half of the merge for EVAL-01 specifically.
GRANT-01 is deliberately NOT made configurable here: its authoritative run is `scripts/
check_grants.py` in CI, executed by the prod SP on a self-hosted runner that has no Lakebase
connectivity to this app's store — threading admin config into it would mean giving CI a database
dependency it doesn't otherwise have, a bigger change than this slice takes on.
"""
from __future__ import annotations

import handbook_rules

SEVERITIES = ("BLOCKER", "SUGGESTION", "STYLE")


def effective_rules(overrides: list[dict] | None = None) -> list[dict]:
    """Merge `handbook_rules.RULES` with admin override rows into the EFFECTIVE rule set the
    reviewer prompt (`review_core.build_review_prompt`) grounds on:
      - a hardcoded rule with no override row: passed through UNCHANGED.
      - a hardcoded rule with a DISABLED override: dropped entirely (the LLM prompt says "cite
        SOMENTE estes rule_id" — omitting it here is what actually stops it firing).
      - a hardcoded rule with an ENABLED override: severity_hint/content/citation/params replaced
        by whatever the override sets (unset fields keep the hardcoded value).
      - a custom rule (`is_custom=True`, enabled): appended, in `handbook_rules.RULES`'s shape.
    """
    by_id = {o["rule_id"]: o for o in (overrides or [])}
    out: list[dict] = []
    for rule in handbook_rules.RULES:
        ov = by_id.get(rule["rule_id"])
        if ov is None:
            out.append(dict(rule))
            continue
        if not ov.get("enabled", True):
            continue
        merged = dict(rule)
        if ov.get("severity"):
            merged["severity_hint"] = ov["severity"]
        if ov.get("content"):
            merged["content"] = ov["content"]
        if ov.get("citation"):
            merged["citation"] = ov["citation"]
        if ov.get("params"):
            merged["params"] = dict(ov["params"])
        out.append(merged)
    for ov in (overrides or []):
        if ov.get("is_custom") and ov.get("enabled", True):
            out.append({
                "rule_id": ov["rule_id"],
                "severity_hint": ov.get("severity") or "SUGGESTION",
                "citation": ov.get("citation") or f"Regra customizada › {ov['rule_id']}",
                "content": ov.get("content") or "",
            })
    return out


def eval01_config(overrides: list[dict] | None = None, *,
                  default_min_benchmark: int = 2, default_severity: str = "BLOCKER"
                  ) -> tuple[int, str]:
    """The deterministic EVAL-01 backstop's effective `(min_benchmark, severity)` —
    `review_core.finalize_findings` owns the ">= N benchmarks" check itself (it's NOT LLM-judged,
    so it can't be configured via the prompt's rules_block like the other 8 rules). Disabling
    EVAL-01 via override collapses to `min_benchmark=0` (a space's benchmark count is never
    negative, so the check never fires) — an explicit, AUDITED admin choice to turn off a BLOCKER,
    never a silent bypass: it still requires the same admin-gated override write as any other rule
    change."""
    by_id = {o["rule_id"]: o for o in (overrides or [])}
    ov = by_id.get("EVAL-01")
    if ov is None:
        return default_min_benchmark, default_severity
    if not ov.get("enabled", True):
        return 0, (ov.get("severity") or default_severity)
    severity = ov.get("severity") or default_severity
    params = ov.get("params") or {}
    n = params.get("min_benchmarks", default_min_benchmark)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = default_min_benchmark
    if n < 0:
        n = default_min_benchmark
    return n, severity

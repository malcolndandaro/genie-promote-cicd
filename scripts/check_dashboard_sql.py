#!/usr/bin/env python3
"""DASH-SQL gate: does this dashboard's rendered dataset SQL actually run in PRODUCTION?

This is the dashboard's analogue of the Genie eval-RUN. A Genie Space proves quality by re-running its
benchmark questions and scoring a pass-rate; a dashboard has no benchmarks, so the equivalent claim is
narrower but sharper: **every dataset query is valid against the prod catalog with the prod warehouse**.
That catches the failure the whole promotion exists to prevent — "it worked in dev" — because a
rebound query can reference a prod table that does not exist, or a column that differs between
environments, and neither the offline structural check nor `bundle validate` would notice.

Mechanism: `EXPLAIN <query>` for each dataset, via the SQL Statement Execution API against the prod
warehouse. EXPLAIN plans the query (resolving catalogs, tables and columns) WITHOUT executing it or
reading any row — so this validates without moving data.

PARAMETERS: a dataset query may carry `:parameter` markers, which cannot be bound here (their values
come from the viewer at render time). Those datasets are SKIPPED with a `::warning` and counted in the
summary, rather than failed. This is deliberate and is NOT a fail-open: fail-closed applies to
unresolved INFRASTRUCTURE (missing credentials, an unreachable warehouse — all of which fail the job
below), not to a construct the check genuinely cannot evaluate. Silently passing them would be the
real defect, so the coverage is always reported.

Exit codes:
    0 — every evaluable dataset planned successfully (skips are reported, not fatal)
    1 — at least one dataset failed to plan
    2 — the check itself could not run (no warehouse id, or the API was unreachable) — fail closed

Usage:
    python3 scripts/check_dashboard_sql.py build/dashboards/<slug>.lvdash.json \
        --warehouse-id <prod-warehouse-id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workflow_support import gh_escape  # noqa: E402

# A `:name` / `:`Quoted Name`` marker is Databricks dashboard parameter syntax. Detected textually
# because the parameter's declared value lives in the dashboard's own `parameters` block, not in SQL.
_PARAM_HINTS = (":`", ":")

# A plan that begins with this is an analysis failure even though the STATEMENT succeeded — EXPLAIN
# reports planning errors in its output rather than as a statement error. Matching the prefix (instead
# of allowlisting individual error codes like TABLE_OR_VIEW_NOT_FOUND) is what makes a missing COLUMN,
# a type mismatch, an ambiguous reference and every other planning failure fail the gate: allowlisting
# codes meant `UNRESOLVED_COLUMN` passed green, which is exactly the dev/prod drift this gate exists
# to catch.
_PLAN_ERROR_PREFIX = "Error occurred during query planning"
_PLAN_ERROR_TOKENS = ("AnalysisException", "TABLE_OR_VIEW_NOT_FOUND", "UNRESOLVED_COLUMN")


def _strip_sql_noise(sql: str) -> str:
    """Remove comments and string literals so parameter detection reads only real SQL.

    Without this, `-- :x` appended to ANY query made `_is_parameterized` true and the dataset was
    skipped — a one-character bypass of the whole gate. Deliberately simple: this is a heuristic for
    deciding "does this query interpolate a dashboard parameter", not a SQL parser.
    """
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":                     # line comment
            i = sql.find("\n", i)
            if i < 0:
                break
            continue
        if ch == "/" and nxt == "*":                      # block comment
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in ("'", '"'):                              # string literal
            # A parameter marker can be a QUOTED name (`:`Time Window Start``), so a preceding `:`
            # must survive the strip — otherwise stripping the quotes destroys the very marker this
            # function exists to find. Emit the `:` and drop only the quoted body.
            prev = out[-1] if out else ""
            end = sql.find(ch, i + 1)
            i = n if end < 0 else end + 1
            out.append("x" if prev == ":" else " ")         # ':' + name -> still reads as a parameter
            continue
        if ch == "`":                                      # quoted identifier — same reasoning
            prev = out[-1] if out else ""
            end = sql.find("`", i + 1)
            i = n if end < 0 else end + 1
            out.append("x" if prev == ":" else " ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _datasets(doc: dict) -> "list[tuple[str, str]]":
    """``(dataset_name, sql)`` for every dataset that carries a query."""
    out = []
    for dataset in doc.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        name = str(dataset.get("name") or "(sem nome)")
        lines = dataset.get("queryLines")
        if isinstance(lines, list):
            sql = "".join(str(line) for line in lines)
        elif isinstance(lines, str):
            sql = lines
        elif isinstance(dataset.get("query"), str):
            sql = dataset["query"]
        else:
            continue
        if sql.strip():
            out.append((name, sql))
    return out


def _is_parameterized(sql: str) -> bool:
    """Whether the query interpolates a dashboard parameter (so it cannot be planned as written).

    Comments and string literals are stripped FIRST (`_strip_sql_noise`): scanning them let a trailing
    `-- :x` mark any query "parameterized" and skip it, which turned an advisory convenience into a
    one-character bypass of the gate. A `::` cast (`x::int`) is still not a parameter.
    """
    stripped = _strip_sql_noise(sql).replace("::", "")
    return ":`" in stripped or any(
        ch == ":" and nxt.isalpha() for ch, nxt in zip(stripped, stripped[1:]))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a dashboard's dataset SQL against prod")
    parser.add_argument("infile", help="the PRE-RENDERED dashboard artifact")
    parser.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    args = parser.parse_args(argv)

    if not os.path.exists(args.infile):
        print(f"DASH-SQL: nada a checar — {args.infile} não existe (rode scripts/render.sh primeiro).")
        return 0
    if not args.warehouse_id:
        # Fail CLOSED: a missing warehouse id means the gate cannot run, and a gate that cannot run
        # must never report success (AGENTS.md: workflows fail closed).
        print("::error title=DASH-SQL::warehouse id ausente — não é possível validar o SQL do painel "
              "em produção (configure --warehouse-id / DATABRICKS_WAREHOUSE_ID).")
        return 2

    with open(args.infile, encoding="utf-8") as handle:
        doc = json.load(handle)

    datasets = _datasets(doc)
    if not datasets:
        # No queries at all is a STRUCTURAL matter (DASH-03 owns "empty panel"), not this gate's.
        print("✅ DASH-SQL — o painel não tem datasets com consulta; nada a validar.")
        return 0

    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
    except Exception as exc:  # noqa: BLE001 — cannot run => fail closed, never advisory-pass
        print(f"::error title=DASH-SQL::não foi possível autenticar no workspace: {gh_escape(str(exc))}")
        return 2

    failed: list[str] = []
    skipped: list[str] = []
    checked = 0
    for name, sql in datasets:
        if _is_parameterized(sql):
            skipped.append(name)
            print(f"::warning title=DASH-SQL::dataset '{name}' usa parâmetros do painel — "
                  "não é possível validar o SQL isoladamente; verificado apenas estruturalmente.")
            continue
        try:
            response = w.statement_execution.execute_statement(
                warehouse_id=args.warehouse_id, statement=f"EXPLAIN {sql}", wait_timeout="30s")
        except Exception as exc:  # noqa: BLE001 — an API/transport failure is operational
            print(f"::error title=DASH-SQL::falha ao validar o dataset '{name}': "
                  f"{gh_escape(str(exc))}")
            return 2
        status = getattr(response, "status", None)
        state = getattr(getattr(status, "state", None), "value", getattr(status, "state", None))
        if str(state) != "SUCCEEDED":
            error = getattr(status, "error", None)
            message = getattr(error, "message", None) or str(state)
            failed.append(name)
            print(f"::error title=DASH-SQL::dataset '{name}' não é válido em produção: "
                  f"{gh_escape(str(message))}")
            continue
        # EXPLAIN succeeds at the statement level even when the PLAN reports an analysis error, so the
        # returned plan text is inspected too — otherwise a missing table would read as a pass.
        plan = ""
        result = getattr(response, "result", None)
        for row in (getattr(result, "data_array", None) or []):
            plan += " ".join(str(cell) for cell in row)
        if plan.lstrip().startswith(_PLAN_ERROR_PREFIX) or any(t in plan for t in _PLAN_ERROR_TOKENS):
            failed.append(name)
            print(f"::error title=DASH-SQL::dataset '{name}' não resolve em produção: "
                  f"{gh_escape(plan[:400])}")
            continue
        checked += 1

    summary = (f"{checked} dataset(s) validado(s)"
               + (f", {len(skipped)} ignorado(s) por usarem parâmetros" if skipped else ""))
    if failed:
        print(f"🔴 DASH-SQL — {len(failed)} dataset(s) inválido(s) em produção "
              f"({', '.join(failed)}). {summary}.")
        return 1
    if checked == 0 and skipped:
        # Every dataset was skipped, so this gate validated NOTHING while reporting success. Reporting
        # a green check for zero coverage is the fail-open shape this gate must not have: a dashboard
        # whose every query is parameterized gets no SQL validation at all, and the reviewer needs to
        # know that rather than read a ✅.
        print(f"::error title=DASH-SQL::nenhum dataset pôde ser validado — todos os "
              f"{len(skipped)} usam parâmetros do painel. Sem cobertura de SQL, a promoção não pode "
              f"ser liberada por este gate.")
        print(f"🔴 DASH-SQL — cobertura zero ({summary}).")
        return 1
    print(f"✅ DASH-SQL — {summary}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""CI DASH-01..04 gate: is this AI/BI dashboard structurally sound?

The dashboard counterpart of `check_eval.py`. A Genie Space's merge-blocking quality floor is its
benchmark COUNT (EVAL-01); a dashboard has no benchmarks, so its floor is instead the claim it CAN
make deterministically: **every widget is wired to a dataset that exists, and the panel renders
something**. Run against the PRE-RENDERED prod artifact.

Runs fully OFFLINE — the checks are a pure function of the committed JSON, so this needs no
WorkspaceClient and no credentials (unlike `check_dashboard_sql.py`, which validates the SQL against a
live prod warehouse). Findings become real GitHub annotations so the app's check-details panel shows
them, mirroring `check_eval.py`/`check_audience.py`'s escaping and exit-code conventions.

Exit codes:
    0 — no BLOCKER (advisories may still be reported)
    1 — at least one BLOCKER (DASH-01 dangling dataset / DASH-03 empty panel)

Usage:
    python3 scripts/check_dashboard.py build/dashboards/<slug>.lvdash.json [--to prod] [--domain <d>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "genie_reviewer"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dashboard_check  # noqa: E402
from workflow_support import gh_escape  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="DASH-01..04 structural gate for an AI/BI dashboard")
    parser.add_argument("infile", help="the PRE-RENDERED dashboard artifact")
    parser.add_argument("--to", dest="to_env", default=os.environ.get("DASH_TO_ENV", "prod"))
    parser.add_argument("--domain", default=os.environ.get("DOMAIN", "recebiveis"))
    args = parser.parse_args(argv)

    if not os.path.exists(args.infile):
        # Mirrors check_eval.py: nothing rendered for this slug is not a failure of THIS gate.
        print(f"DASH: nada a checar — {args.infile} não existe (rode scripts/render.sh primeiro).")
        return 0

    with open(args.infile, encoding="utf-8") as handle:
        doc = json.load(handle)

    findings = dashboard_check.check_dashboard(doc, to_env=args.to_env, domain=args.domain)
    blockers = [f for f in findings if f["severity"] == "BLOCKER"]

    for finding in findings:
        level = "error" if finding["severity"] == "BLOCKER" else "warning"
        print(f"::{level} title={finding['rule_id']}::{gh_escape(finding['message'])}")

    if blockers:
        print(f"🔴 {len(blockers)} checagem(ns) estrutural(is) do painel bloqueando a promoção "
              f"({', '.join(sorted({f['rule_id'] for f in blockers}))}).")
        return 1
    advisories = len(findings)
    print(f"✅ Estrutura do painel OK ({advisories} aviso(s) informativo(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

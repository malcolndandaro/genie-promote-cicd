"""CI EVAL-01 gate (pr-checks). Does the pre-rendered, prod serialized_space carry at least the
required number of benchmark questions (Q→SQL)?

WHY a CI gate (not just the app's own review): the app-side EVAL-01 blocks the app's gate, but the
merge itself is enforced on GitHub. This script makes a space with too few benchmarks FAIL the
`pr-checks` job, so branch protection can block the merge. It's the deterministic COUNT half of the
eval story — a floor gate. The full eval-RUN (Genie re-runs the questions and scores a pass-rate)
CANNOT run here: it needs a LIVE Genie space with authored benchmarks and credentials to the
workspace it lives in, and at pr-checks time the prod space doesn't exist yet (created post-merge by
`bundle deploy`) and CI has no dev credentials. So the pass-rate stays the app's advisory/gate; the
merge-blocking gate is this benchmark COUNT, which IS deterministically readable from the committed
JSON (`benchmarks.questions` round-trips through the create/import API — SP1 spike, 2026-07-02).

Runs offline: the count is read straight from the file, so — unlike the audience check — this needs NO
WorkspaceClient / prod credentials. It just needs the rendered artifact.

Threshold: `rules_config.eval01_config(None)` → the hardcoded default (min_benchmark=2, BLOCKER).
CI is a plain checkout with no Lakebase, so the admin's rule-override store isn't reachable here;
`EVAL_MIN_BENCHMARKS` env overrides the floor if a deployment wants a different CI minimum.

Usage:
    python3 scripts/check_eval.py [rendered_space.json]
Local test:
    python3 scripts/check_eval.py build/genie/receivables.serialized_space.json
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import review_core  # noqa: E402  (build_space_context — the SAME n_benchmark the app computes)
import rules_config  # noqa: E402  (eval01_config — the SAME (min, severity) resolution)

DEFAULT_PATH = "build/genie/receivables.serialized_space.json"


def _gh_escape(s: str) -> str:
    """Escape text for a GitHub Actions workflow-command payload (`::error::`/`::warning::`), so the
    finding becomes a real check-run annotation. Per the docs, `%` must be escaped first. Mirrors
    the audience check's escaping verbatim."""
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_annotation(level: str, message: str) -> None:
    """Print an EVAL-01 finding as a REAL GitHub annotation (a bare print never becomes one). `level`
    is `"error"` for the blocking count miss, `"warning"` otherwise — matches the audience check's shape
    so `github_app._summarize_annotations` surfaces it in the app's check-details panel."""
    print(f"::{level} title=EVAL-01::{_gh_escape(message)}")


def _min_benchmark() -> int:
    """The required benchmark count: the EVAL-01 hardcoded default (2), overridable via
    `EVAL_MIN_BENCHMARKS` for a deployment that wants a different CI floor. `eval01_config(None)` is
    the SAME pure resolver the app uses (with no overrides, since CI can't reach the Lakebase store)."""
    default_min, _ = rules_config.eval01_config(None)
    raw = os.environ.get("EVAL_MIN_BENCHMARKS")
    if raw is None:
        return default_min
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default_min
    return n if n >= 0 else default_min


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.exists(path):
        print(f"EVAL-01: nada a checar — {path} não existe (rode scripts/render.sh primeiro).")
        return 0
    with open(path, encoding="utf-8") as fh:
        space = json.load(fh)

    # The SAME count the app's reviewer uses (benchmarks.questions), via the shared pure helper.
    n = review_core.build_space_context(space).get("n_benchmark", 0)
    min_benchmark = _min_benchmark()

    if n < min_benchmark:
        msg = (f"O espaço tem {n} pergunta(s) de benchmark; produção exige >= {min_benchmark}. "
               f"Sem elas não há o que o eval-run certifique — promoção bloqueada.")
        _emit_annotation("error", msg)
        print(f"🔴 EVAL-01 — promoção bloqueada: {msg}")
        return 1
    print(f"✅ EVAL-01 — {n} pergunta(s) de benchmark (>= {min_benchmark}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

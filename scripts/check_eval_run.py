"""CI EVAL-RUN gate (pr-checks) — the eval-run PASS-RATE, blocking the merge.

Complements scripts/check_eval.py (the benchmark COUNT floor): this actually RE-RUNS the space's
benchmark questions via Genie and blocks the PR when the pass-rate is below the threshold. The
eval-run needs a LIVE Genie space with authored benchmarks — that's the DEV space being promoted
(the prod space doesn't exist until post-merge deploy), so this authenticates to the DEV workspace
as the dev-reader SP (oauth-m2m from the CI env) and runs against the dev space id.

Resolving the dev space id (the eval-run's target): the promotion's committed file/branch is keyed
by a SLUG (see app_logic.space_slug). A pinned slug (APP_SPACE_SLUGS, JSON {space_id: slug}) inverts
back to its id; a default slug is `s_<devspaceid>` → strip the `s_`. We deliberately do NOT match by
`.title` here (unlike apply_access): the `.title` sidecar carries the PROD title, which the requester
may have edited, and the dev space's own title can differ — the slug is the exact, workspace-agnostic
key.

Blocking policy (mirrors the app gate, commit 76cb674): only a `block` verdict (pass-rate < threshold)
fails the job (exit 1). `advisory` (eval-run unavailable, still-running past the budget, or no
benchmarks authored) is NON-blocking (exit 0) — the eval-run is slow/flaky by nature, and a floor
already exists (check_eval.py's COUNT); a transient eval-run outage must never wedge legitimate
promotions. Threshold from rules_config.eval_run_threshold (default 0.8), overridable via
EVAL_RUN_THRESHOLD (a 0..1 fraction).

Usage:
    python3 scripts/check_eval_run.py <rendered_space.json>   # slug comes from the filename
Local test against a profile (targets whatever workspace the profile points at):
    DATABRICKS_CONFIG_PROFILE=cerc-mlops-dev python3 scripts/check_eval_run.py build/genie/receivables.serialized_space.json
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import eval_gate  # noqa: E402
import rules_config  # noqa: E402

from databricks.sdk import WorkspaceClient


def _gh_escape(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_annotation(level: str, message: str) -> None:
    print(f"::{level} title=EVAL-RUN::{_gh_escape(message)}")


def slug_from_path(path: str) -> str:
    """`build/genie/<slug>.serialized_space.json` → `<slug>`."""
    base = os.path.basename(path)
    return base[:-len(".serialized_space.json")] if base.endswith(".serialized_space.json") else base


def dev_space_id_from_slug(slug: str) -> "str | None":
    """Recover the DEV Genie space id the eval-run targets, from the promotion slug. A pinned slug
    (APP_SPACE_SLUGS = JSON {space_id: slug}) inverts back to its id; otherwise the default slug is
    `s_<devspaceid>` (app_logic.space_slug), so strip the `s_`. Returns None if neither applies (a
    slug that's neither pinned nor `s_`-prefixed — can't resolve, caller degrades to advisory)."""
    try:
        pins = json.loads(os.environ.get("APP_SPACE_SLUGS") or "{}")
    except (ValueError, TypeError):
        pins = {}
    for space_id, pinned_slug in pins.items():
        if pinned_slug == slug:
            return space_id
    if slug.startswith("s_"):
        return slug[len("s_"):]
    return None


def _threshold() -> float:
    default = rules_config.eval_run_threshold(None)  # 0.8; CI has no Lakebase override store
    raw = os.environ.get("EVAL_RUN_THRESHOLD")
    if raw is None:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if 0.0 <= v <= 1.0 else default


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_eval_run.py <rendered_space.json>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"EVAL-RUN: nada a checar — {path} não existe (rode scripts/render.sh primeiro).")
        return 0

    slug = slug_from_path(path)
    dev_space_id = dev_space_id_from_slug(slug)
    if not dev_space_id:
        # Can't resolve the dev space id from the slug → advisory (never block on an unresolvable id).
        print(f"🟡 EVAL-RUN (advisory) — não foi possível resolver o dev space id do slug {slug!r}; "
              f"não bloqueia (a contagem EVAL-01 continua como piso).")
        return 0

    threshold = _threshold()
    # The eval-run targets the DEV workspace: in CI, the dev-reader SP via oauth-m2m env
    # (DATABRICKS_HOST=dev, DATABRICKS_CLIENT_ID/SECRET=dev SP); locally, DATABRICKS_CONFIG_PROFILE.
    w = WorkspaceClient()
    res = eval_gate.run_eval_gate_rest(dev_space_id, client=w, threshold=threshold)
    status = res.get("status")
    summary = res.get("summary") or ""

    if status == "block":
        _emit_annotation("error", summary or f"eval-run abaixo do limiar de {threshold:.0%}.")
        print(f"🔴 EVAL-RUN — promoção bloqueada: {summary}")
        return 1
    if status == "advisory":
        print(f"🟡 EVAL-RUN (advisory, não bloqueia) — {summary}")
        return 0
    print(f"✅ EVAL-RUN — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

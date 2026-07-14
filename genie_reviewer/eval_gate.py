"""Eval-run quality gate (S7, REST-ified W2).

Runs the Genie benchmark eval, gates promotion on pass-rate, and DEGRADES GRACEFULLY
(advisory, non-blocking) when there are no benchmark questions or the endpoint is
unavailable — promotion is never hard-failed on eval infra. The floor that still
protects quality is the agent's static EVAL-01 (≥2 benchmark Q→SQL) + the human approval.

Benchmark questions are authored in the Genie UI (not in serialized_space; no CLI to
create them), so a space without authored benchmarks hits the advisory path — the common
case, and exactly what "graceful degradation" means here.

Two gates share the same `decide_eval` verdict logic:
  - `run_eval_gate` — the ORIGINAL, shells out to the `databricks` CLI. Kept for local/script
    use (a profile + the CLI on PATH); the deployed app's Apps container has no CLI, which is
    why it always degraded to advisory there.
  - `run_eval_gate_rest` (W2) — the one the app actually uses now: the typed Genie eval-run SDK
    (`WorkspaceClient.genie.genie_create_eval_run`/`genie_get_eval_run`), so a real eval-run runs
    from the deployed app too. Verified live against the dev demo space (24 benchmark questions):
    `genie_create_eval_run` returns a `GenieEvalRunResponse` immediately (status RUNNING/
    NOT_STARTED); `genie_get_eval_run` polls the SAME shape until a terminal `eval_run_status`
    (DONE/EVALUATION_CANCELLED/EVALUATION_FAILED/EVALUATION_TIMEOUT); a DONE run carries
    `num_correct`/`num_done`/`num_needs_review`/`num_questions` — `num_done` was absent on
    in-flight (RUNNING) list entries but present once terminal, so the pass-rate is only computed
    at DONE, never inferred from a partial count.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

_PASS_TOKENS = {"PASS", "PASSED", "SUCCESS", "CORRECT", "TRUE", "OK"}
_NO_BENCH = "no benchmark"

# W2: how long run_eval_gate_rest waits for a real eval-run to reach a terminal status before
# degrading to "in progress, not awaited" — an eval-run can outlive one review request, and this
# gate must never block a promotion review indefinitely on it. Config-driven (ADR-0004).
_DEFAULT_WAIT_SECONDS = float(os.environ.get("APP_EVAL_WAIT_SEC", "45"))
_TERMINAL_STATUSES = {"DONE", "EVALUATION_CANCELLED", "EVALUATION_FAILED", "EVALUATION_TIMEOUT"}


def _passed(item: dict) -> bool:
    """Tolerant: a per-question result counts as passed via a bool flag or a status token."""
    if not isinstance(item, dict):
        return False
    for k in ("passed", "is_correct", "correct", "success"):
        if isinstance(item.get(k), bool):
            return item[k]
    for k in ("status", "result", "outcome", "grade"):
        v = item.get(k)
        if isinstance(v, str) and v.strip().upper() in _PASS_TOKENS:
            return True
    return False


def decide_eval(results, threshold: float = 0.8, available: bool = True, reason: str = "") -> dict:
    """Pure gate decision. `results` = per-question outcome dicts. available=False or empty
    => advisory (non-blocking). pass-rate < threshold => block; else pass."""
    if not available:
        return {"status": "advisory", "pass_rate": None, "n": 0,
                "summary": f"🟡 eval-run não executado ({reason}) — não bloqueia (degradação graciosa)."}
    items = [r for r in (results or []) if isinstance(r, dict)]
    n = len(items)
    if n == 0:
        return {"status": "advisory", "pass_rate": None, "n": 0,
                "summary": "🟡 sem perguntas de benchmark — não bloqueia (autoria no Genie / EVAL-01)."}
    n_pass = sum(1 for it in items if _passed(it))
    rate = n_pass / n
    if rate < threshold:
        return {"status": "block", "pass_rate": rate, "n": n,
                "summary": f"🔴 eval-run {n_pass}/{n} ({rate:.0%}) < limiar {threshold:.0%} — promoção bloqueada."}
    return {"status": "pass", "pass_rate": rate, "n": n,
            "summary": f"✅ eval-run {n_pass}/{n} ({rate:.0%}) >= limiar {threshold:.0%}."}


def _genie(profile: str, *args: str) -> subprocess.CompletedProcess:
    # Catch OSError (e.g. databricks binary not on PATH) so run_eval_gate truly never
    # raises — a CLI-exec failure becomes a non-zero result → advisory degradation.
    try:
        return subprocess.run(["databricks", "genie", *args, "-p", profile, "-o", "json"],
                              capture_output=True, text=True)
    except OSError as e:
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr=f"cli indisponível: {e}")


def run_eval_gate(space_id: str, profile: str, threshold: float = 0.8) -> dict:
    """Live gate: create-eval-run → list-eval-results → decide. Never raises; degrades to
    advisory on no-benchmarks / unavailable / unreadable. (Polling of get-eval-run for async
    completion is a [VERIFY] follow-up against a space that has authored benchmarks.)"""
    cr = _genie(profile, "genie-create-eval-run", space_id)
    if cr.returncode != 0:
        reason = ("sem perguntas de benchmark" if _NO_BENCH in (cr.stderr or "").lower()
                  else (cr.stderr or "erro").strip()[:120])
        return decide_eval(None, threshold, available=False, reason=reason)
    try:
        created = json.loads(cr.stdout or "{}")
    except ValueError:
        return decide_eval(None, threshold, available=False, reason="resposta de create ilegível")
    run_id = created.get("eval_run_id") or created.get("id") or created.get("run_id")
    if not run_id:
        return decide_eval(None, threshold, available=False, reason="sem eval_run_id na resposta")
    lr = _genie(profile, "genie-list-eval-results", space_id, str(run_id))
    if lr.returncode != 0:
        return decide_eval(None, threshold, available=False, reason="list-eval-results falhou")
    try:
        data = json.loads(lr.stdout or "{}")
    except ValueError:
        return decide_eval(None, threshold, available=False, reason="resultados ilegíveis")
    results = (data.get("results") or data.get("eval_results")
               or (data if isinstance(data, list) else []))
    return decide_eval(results, threshold)


def _status_name(status) -> str:
    """Normalize an `EvaluationStatusType` (SDK enum) or a plain string to its bare name (mirrors
    `app_logic._priv_name`'s enum/str normalization) — tests exercise both shapes."""
    return getattr(status, "value", status) or ""


def run_eval_gate_rest(space_id: str, *, client, threshold: float = 0.8,
                       wait_seconds: float | None = None, poll_interval: float = 5.0) -> dict:
    """REST/SDK-based eval-run gate (W2) — what the deployed app actually calls now, in place of
    `run_eval_gate`'s `databricks` CLI subprocess (absent on the Apps container). `client` is a
    `WorkspaceClient` (or a fake exposing the same `.genie.genie_create_eval_run`/
    `.genie.genie_get_eval_run` shape) targeting the workspace the Space lives in — the caller
    resolves that (dev-sp or a profile; see `app_logic.review_space`).

    Creates the run, then polls `genie_get_eval_run` until `eval_run_status` reaches a terminal
    value or `wait_seconds` elapses (default `APP_EVAL_WAIT_SEC`, ~45s) — a real eval-run can
    outlive one review request, so this NEVER blocks a promotion review indefinitely on it: a
    still-running run past the budget degrades to an advisory "in progress, not awaited" result
    rather than a timeout error.

    NEVER raises: any failure (no benchmark questions authored, the eval-run API unreachable, a
    poll error, a non-DONE terminal status) degrades to the SAME advisory `decide_eval` path the
    CLI gate uses — an eval-run is a quality signal, never infrastructure a promotion can be
    hard-blocked on.

    Pass-rate is `num_correct / num_done` at DONE — a question still `needs_review` (not yet a
    confirmed GOOD/BAD assessment) counts as NOT passed, the conservative read: an ambiguous
    result should never look like a pass. Reuses `decide_eval` (a synthetic per-question list of
    that many passes/fails) for the actual threshold/severity decision + PT copy, so there is ONE
    place that owns the pass/block boundary regardless of which gate produced the counts.
    """
    wait_seconds = _DEFAULT_WAIT_SECONDS if wait_seconds is None else wait_seconds
    try:
        run = client.genie.genie_create_eval_run(space_id)
    except Exception as e:  # noqa: BLE001 — e.g. no benchmark questions authored on this Space
        reason = _NO_BENCH if _NO_BENCH in str(e).lower() else str(e)[:160]
        return decide_eval(None, threshold, available=False, reason=reason)

    deadline = time.monotonic() + wait_seconds
    status = _status_name(run.eval_run_status)
    while status not in _TERMINAL_STATUSES and time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            run = client.genie.genie_get_eval_run(space_id, run.eval_run_id)
        except Exception as e:  # noqa: BLE001 — transient poll failure: degrade, never crash the review
            return decide_eval(None, threshold, available=False, reason=f"poll de eval-run falhou: {e}")
        status = _status_name(run.eval_run_status)

    if status not in _TERMINAL_STATUSES:
        return {"status": "advisory", "pass_rate": None, "n": run.num_questions or 0,
                "summary": "🟡 eval-run em execução — resultado não aguardado (acompanhe no Genie)."}
    if status != "DONE":
        return decide_eval(None, threshold, available=False,
                           reason=f"eval-run terminou como {status.lower()}")

    n_done = run.num_done or 0
    if n_done == 0:
        return decide_eval([], threshold)  # -> "sem perguntas de benchmark" advisory
    n_correct = run.num_correct or 0
    synthetic = [{"passed": True}] * n_correct + [{"passed": False}] * max(n_done - n_correct, 0)
    return decide_eval(synthetic, threshold)

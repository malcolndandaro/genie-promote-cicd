"""Eval-run quality gate (S7).

Runs the Genie benchmark eval, gates promotion on pass-rate, and DEGRADES GRACEFULLY
(advisory, non-blocking) when there are no benchmark questions or the endpoint is
unavailable — promotion is never hard-failed on eval infra. The floor that still
protects quality is the agent's static EVAL-01 (≥3 benchmark Q→SQL) + the human approval.

Benchmark questions are authored in the Genie UI (not in serialized_space; no CLI to
create them), so a space without authored benchmarks hits the advisory path — the common
case, and exactly what "graceful degradation" means here.

[VERIFY] the live result field shape (`genie-list-eval-results`) against a space with
authored benchmark questions; `_passed` is intentionally tolerant of field naming.
"""
from __future__ import annotations

import json
import subprocess

_PASS_TOKENS = {"PASS", "PASSED", "SUCCESS", "CORRECT", "TRUE", "OK"}
_NO_BENCH = "no benchmark"


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

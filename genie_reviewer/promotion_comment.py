"""Render the in-app review as a GitHub PR comment (the "UI mirror", GH2).

Pure function: review (+ requester) -> markdown. Mirrors the SPA's Revisão screen so the PR shows
the same pipeline steps + gate + findings/blockers a business author sees, attributed to the human
(OBO) requester even though a bot posts it. Kept dependency-free + side-effect-free so it is unit
tested in isolation and stays in sync with the UI's fields.
"""
from __future__ import annotations

# A hidden marker so the bot updates ONE canonical comment instead of spamming the PR.
PROMOTION_COMMENT_MARKER = "<!-- genie-promote:promotion-summary -->"

_STEP_ICON = {"pass": "✅", "fail": "❌", "running": "⏳", "pending": "⬜"}
_STEP_WORD = {"pass": "concluído", "fail": "reprovado", "running": "em execução", "pending": "pendente"}


def _safe(text: object) -> str:
    """Neutralize HTML-comment markers in free text — so an LLM/finding string can't inject a
    second copy of the upsert marker (which would break in-place comment matching) or HTML."""
    return str(text or "").replace("<!--", "").replace("-->", "")


def _gate_line(gate: dict) -> str:
    summary = _safe(gate.get("summary"))
    if gate.get("conclusion") == "failure":
        return f"### 🔴 Gate: bloqueado\n\n{summary}"
    if gate.get("conclusion") == "success":
        return f"### 🟢 Gate: liberado\n\n{summary}"
    return f"### 🟡 Gate: avisos (não bloqueiam)\n\n{summary}"


def render_promotion_comment(review: dict, requester_email: str | None) -> str:
    """Markdown mirroring the Revisão UI. First line is the marker (for in-place upsert)."""
    who = _safe(requester_email) or "usuário autenticado"
    lines: list[str] = [
        PROMOTION_COMMENT_MARKER,
        "## Revisão de promoção (Genie/AI-BI)",
        "",
        f"**Solicitado por:** `{who}` · _Achados gerados por IA — verifique antes de aprovar._",
        "",
        "_Leitura dos espaços executa como o usuário (OBO); o revisor (LLM) e a checagem de "
        "grants executam como o service principal do app._",
        "",
        "### Pipeline de promoção",
        "",
    ]
    for step in review.get("timeline", []):
        status = step.get("status", "pending")
        icon = _STEP_ICON.get(status, "⬜")
        word = _STEP_WORD.get(status, status)
        lines.append(f"- {icon} {_safe(step.get('label', step.get('key', '')))} — _{word}_")

    lines += ["", _gate_line(review.get("gate", {})), "", "### Achados do Genie Reviewer", ""]
    findings = review.get("findings", [])
    if not findings:
        lines.append("Nenhum achado — espaço limpo.")
    else:
        for f in findings:
            lines.append(
                f"- **{_safe(f.get('severity'))} · {_safe(f.get('rule_id'))}** — {_safe(f.get('message'))}")
            if f.get("suggestion"):
                lines.append(f"  - ↳ {_safe(f['suggestion'])}")
            if f.get("citation"):
                lines.append(f"  - _{_safe(f['citation'])}_")

    eval_summary = (review.get("eval") or {}).get("summary")
    if eval_summary:
        lines += ["", f"**eval-run:** {_safe(eval_summary)}"]
    return "\n".join(lines)

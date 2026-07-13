"""Render the in-app review as a GitHub PR comment (the "UI mirror", GH2).

Pure function: review (+ requester) -> markdown. Mirrors the SPA's Revisão screen so the PR shows
the same pipeline steps + gate + findings/blockers a business author sees, attributed to the human
(OBO) requester even though a bot posts it. Kept dependency-free + side-effect-free so it is unit
tested in isolation and stays in sync with the UI's fields.
"""
from __future__ import annotations

# A hidden marker tagging every review comment the bot posts — a re-request POSTS A NEW comment
# (history on the PR timeline), never PATCHes a prior one; `marker` is how a caller recognizes
# "one of ours" and picks the LATEST if it needs the current state.
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


def _mapping_lines(table_mapping: dict | None) -> list[str]:
    """G7: the promotion's declared table de-para, rendered as a markdown table — so the Steward/
    reviewer see it in the comment itself, not just by digging into the sidecar's own PR diff."""
    if not table_mapping:
        return []
    lines = ["", "### De-para de tabelas (dev → prod)", "",
             "| Origem (dev) | Destino (prod) |", "| --- | --- |"]
    for source, target in table_mapping.items():
        lines.append(f"| `{_safe(source)}` | `{_safe(target)}` |")
    return lines


def render_promotion_comment(review: dict, requester_email: str | None, *,
                             resource_title: str | None = None,
                             table_mapping: dict | None = None) -> str:
    """Markdown mirroring the Revisão UI. First line is the marker (every review comment carries
    it — GitHub history is NEW comments, not one canonical comment mutated in place).

    The visible "Revisão #N — <timestamp> UTC" header is deliberately NOT added here: it depends
    on GitHub's own comment history + clock, which this function stays pure/side-effect-free about
    (see the module docstring) — it's injected downstream by
    ``github_app.GitHubApp.post_review_comment``.

    ``resource_title``/``table_mapping`` (G7, optional) are the Requester's declared prod Space
    name + table de-para — surfaced here for transparency, in ADDITION to being visible in the
    sidecars' own PR diff (``.title``/``.mapping.json``)."""
    who = _safe(requester_email) or "usuário autenticado"
    lines: list[str] = [
        PROMOTION_COMMENT_MARKER,
        "## Revisão de promoção (Genie/AI-BI)",
        "",
        f"**Solicitado por:** `{who}` · _Achados gerados por IA — verifique antes de aprovar._",
    ]
    if resource_title:
        lines.append(f"**Nome do space em produção:** `{_safe(resource_title)}`")
    lines += [
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

    lines += ["", _gate_line(review.get("gate", {}))]
    lines += _mapping_lines(table_mapping)
    lines += ["", "### Achados do Genie Reviewer", ""]
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

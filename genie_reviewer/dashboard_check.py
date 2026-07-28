"""dashboard_check — the deterministic structural gate for an AI/BI dashboard (DASH-01..04).

WHY this exists: a Genie Space's quality floor is its eval benchmarks — `benchmarks.questions`
(counted deterministically by EVAL-01) plus the eval-RUN pass-rate. A dashboard has NO benchmark
questions, so those two rules can never be satisfied by one and must not fire on it
(`resource_kind.DASHBOARD_KIND.has_benchmarks is False`).

Rather than degrade the eval story to "advisory, nothing checked", this module replaces it with the
claim a dashboard CAN make deterministically: **its widgets are actually wired to its datasets, and
it actually renders something**. That is a genuine merge-blocking gate, not a softened one — and
unlike an eval-run it needs no live workspace, so it is computable offline in `pr-checks` from the
committed artifact alone. (The complementary "does the SQL actually run in prod" half is
`scripts/check_dashboard_sql.py`, which does need prod credentials.)

Pure and I/O-free, exactly like `audience_check` / `review_core` — the whole module is a function of
one parsed `.lvdash.json`, so it is unit-testable with a dict literal and no fakes.

The four rules:

  DASH-01  BLOCKER     a widget query names a `datasetName` no dataset defines -> the panel is
                       broken on arrival (it renders an error, not data).
  DASH-02  SUGGESTION  a dataset no widget references -> dead query. Advisory: it costs nothing at
                       render time and blocking it would punish work-in-progress.
  DASH-03  BLOCKER     no page, or no widget on any page -> an empty shell. Nothing to certify.
  DASH-04  SUGGESTION  a text/markdown widget's PROSE mentions a catalog outside the target env.

DASH-04 is deliberately advisory, and that asymmetry is the point. A dashboard's only data-access
path is `datasets[].queryLines`, which ENV-01 gates strictly (see `pre_render.dashboard_sql_text`).
Prose is not a data path — no query runs from a text widget — so a stale catalog name in a heading is
a documentation defect, not a data leak. The whole-document rebind already rewrites it to the target
env; this rule just makes the leftover visible instead of silent. It is also why a markdown URL
(`https://en.wikipedia.org/...`, which the 3-part-ref grammar happily matches as catalog `en`) can
surface here harmlessly, where as a BLOCKER it would have blocked a perfectly good dashboard.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import pre_render  # noqa: E402  (dashboard_sql_text / dashboard_prose_text / find_violations)

CITATION_PREFIX = "Genie Promotion Handbook › Dashboards › "

# The DASH-* rules as the reviewer's grounded rule set sees them (same shape as
# `handbook_rules.RULES`). Kept HERE, next to the code that enforces them, so a rule and its
# implementation can't drift; `handbook_rules` imports them so the admin Regras UI still sees one
# merged list.
RULES = [
    {
        "rule_id": "DASH-01",
        "severity_hint": "BLOCKER",
        "citation": CITATION_PREFIX + "DASH-01",
        "content": (
            "Todo widget de um painel deve referenciar um dataset que exista na definição. "
            "Um `datasetName` órfão faz o painel renderizar erro em produção. BLOCKER."
        ),
    },
    {
        "rule_id": "DASH-02",
        "severity_hint": "SUGGESTION",
        "citation": CITATION_PREFIX + "DASH-02",
        "content": (
            "Um dataset que nenhum widget usa é consulta morta: não aparece para ninguém e "
            "ainda assim precisa ser mantido. Remova antes de promover."
        ),
    },
    {
        "rule_id": "DASH-03",
        "severity_hint": "BLOCKER",
        "citation": CITATION_PREFIX + "DASH-03",
        "content": (
            "Um painel de produção deve ter ao menos uma página com ao menos um widget. "
            "Um painel vazio não tem o que certificar. BLOCKER."
        ),
    },
    {
        "rule_id": "DASH-04",
        "severity_hint": "SUGGESTION",
        "citation": CITATION_PREFIX + "DASH-04",
        "content": (
            "Texto/markdown do painel que cita catálogo de outro ambiente é defeito de "
            "documentação, não vazamento de dados (nenhuma query roda de um widget de texto). "
            "O rebind já reescreve o texto; confira se ficou correto. Não bloqueia."
        ),
    },
]


def _widgets(doc: dict) -> "list[dict]":
    """Every widget on every page, flattened. Tolerant of a missing/odd `layout` entry."""
    out: list[dict] = []
    for page in doc.get("pages") or []:
        if not isinstance(page, dict):
            continue
        for entry in page.get("layout") or []:
            if isinstance(entry, dict) and isinstance(entry.get("widget"), dict):
                out.append(entry["widget"])
    return out


def _dataset_names(doc: dict) -> "list[str]":
    return [d["name"] for d in (doc.get("datasets") or [])
            if isinstance(d, dict) and isinstance(d.get("name"), str) and d["name"]]


def _referenced_datasets(widget: dict) -> "set[str]":
    """The dataset names one widget's queries reference."""
    names: set[str] = set()
    for query in widget.get("queries") or []:
        if not isinstance(query, dict):
            continue
        spec = query.get("query")
        if isinstance(spec, dict) and isinstance(spec.get("datasetName"), str):
            names.add(spec["datasetName"])
    return names


def _finding(rule_id: str, severity: str, message: str, suggestion: "str | None") -> dict:
    return {
        "severity": severity,
        "rule_id": rule_id,
        "citation": CITATION_PREFIX + rule_id,
        "message": message,
        "suggestion": suggestion,
    }


def check_dashboard(doc: dict, *, to_env: str = "prod", domain: str = "recebiveis") -> "list[dict]":
    """Every deterministic DASH-* finding for one dashboard definition, in rule order.

    `doc` is the PARSED `.lvdash.json` (already rebound to `to_env` by the caller, since DASH-04
    reports on the post-rebind prose — a pre-rebind check would flag every dashboard). `to_env`/
    `domain` are only used by DASH-04's advisory catalog scan; the structural rules ignore them.

    Findings carry their own `rule_id` and severity, exactly like `audience_check`'s, so
    `review_core.finalize_findings` treats them as deterministic-owned and the LLM cannot soften them.
    """
    findings: list[dict] = []
    widgets = _widgets(doc)
    defined = _dataset_names(doc)
    defined_set = set(defined)

    # DASH-03 first: on an empty dashboard the other rules have nothing to say, and reporting
    # "no widgets" is far more actionable than a list of unused datasets.
    if not widgets:
        findings.append(_finding(
            "DASH-03", "BLOCKER",
            "O painel não tem nenhum widget (nenhuma página com conteúdo) — nada a promover.",
            "Adicione ao menos uma visualização ao painel no workspace de dev antes de promover.",
        ))

    # DASH-01: a widget pointing at a dataset that doesn't exist.
    referenced: set[str] = set()
    for widget in widgets:
        names = _referenced_datasets(widget)
        referenced |= names
        for name in sorted(names - defined_set):
            findings.append(_finding(
                "DASH-01", "BLOCKER",
                f"o widget {widget.get('name') or '(sem nome)'!r} referencia o dataset "
                f"{name!r}, que não existe na definição do painel.",
                "Corrija a consulta do widget no painel em dev (ou recrie o dataset) e promova novamente.",
            ))

    # DASH-02: a dataset nothing renders.
    for name in [n for n in defined if n not in referenced]:
        findings.append(_finding(
            "DASH-02", "SUGGESTION",
            f"o dataset {name!r} não é usado por nenhum widget (consulta morta).",
            "Remova o dataset não utilizado, ou adicione um widget que o use.",
        ))

    # DASH-04: advisory prose scan (never a BLOCKER — see the module docstring).
    prose = pre_render.dashboard_prose_text(doc)
    if prose:
        stray = pre_render.find_violations(prose, to_env, domain)
        if stray:
            findings.append(_finding(
                "DASH-04", "SUGGESTION",
                f"texto do painel menciona catálogo fora de {to_env}_{domain}: "
                f"{', '.join(stray)} (apenas documentação — nenhuma query roda de um widget de texto).",
                "Revise os textos/títulos do painel; o conteúdo de dados já foi reapontado para "
                f"{to_env}_{domain}.",
            ))

    return findings

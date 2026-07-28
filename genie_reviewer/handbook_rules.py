"""The Genie Promotion Handbook as structured rules (S5).

The handbook is small, so the agent grounds on ALL applicable rules in-context and cites a rule_id
per finding — no Vector Search needed (that's the scale path, kept as a future option). Source of
truth: handbook/genie-handbook.md. Keep in sync.

KIND SCOPING: most rules apply to every promotable resource, but two families do not — the benchmark
rules (EVAL-01/EVAL-02) are meaningless for an AI/BI dashboard, which has no benchmark questions, and
the dashboard structural rules (DASH-*) are meaningless for a Genie Space. Rather than let a rule
fire on an artifact that could never satisfy it, each rule carries an optional ``kinds`` tuple naming
the resource kinds it applies to; a rule with no ``kinds`` applies to ALL kinds.
`rules_config.effective_rules(kind=...)` does the filtering, so `RULES` stays one merged list and the
admin Regras UI keeps showing a single rule inventory.
"""
import dashboard_check
import resource_kind

# Column-name signal terms for PII / bank-secrecy (LGPD, sigilo bancário). Lowercase.
PII_SIGNAL_TERMS = ("cpf", "titular", "portador", "pan", "cartao", "cartão")

_GENIE_ONLY = (resource_kind.GENIE_SPACE,)

_BASE_RULES = [
    {
        "rule_id": "ENV-01",
        "severity_hint": "BLOCKER",
        "citation": "Genie Promotion Handbook › Catalog-per-Env › ENV-01",
        "content": (
            "Um espaço de produção deve referenciar apenas catálogos prod_<domínio>. "
            "Qualquer data_source ou SQL apontando para dev_/sbx_/outro ambiente é BLOCKER."
        ),
    },
    {
        "rule_id": "ENV-02",
        "severity_hint": "SUGGESTION",
        "citation": "Genie Promotion Handbook › Catalog-per-Env › ENV-02",
        "content": "O warehouse do espaço deve ser o de produção, não um de dev.",
    },
    {
        "rule_id": "PII-01",
        "severity_hint": "BLOCKER",
        "citation": "Genie Promotion Handbook › PII › PII-01",
        "content": (
            "BLOCKER APENAS quando há exposição indevida real: coluna de PII de pessoa física "
            "ou sigilo bancário (CPF, identidade/dados do titular, número de cartão/PAN) exposta "
            "SEM máscara a um grupo não autorizado. CNPJ de cedente é identificador de EMPRESA "
            "(dado de negócio) e NÃO é bloqueador por si só. Risco BCB-538."
        ),
    },
    {
        "rule_id": "PII-02",
        "severity_hint": "SUGGESTION",
        "citation": "Genie Promotion Handbook › PII › PII-02",
        "content": "Instruções não devem induzir cruzamento entre clientes nem vazamento entre tenants.",
    },
    {
        "rule_id": "EVAL-01",
        "severity_hint": "BLOCKER",
        "citation": "Genie Promotion Handbook › Quality › EVAL-01",
        "content": (
            "Um espaço de produção deve ter >= 2 perguntas de benchmark (Q->SQL). Sem elas "
            "não há o que certificar pelo eval-run. BLOCKER."
        ),
        # Benchmarks are a Genie concept: a dashboard has no `benchmarks.questions`, so this rule
        # could never be satisfied by one. Its dashboard counterpart is the DASH-* family below.
        "kinds": _GENIE_ONLY,
    },
    {
        "rule_id": "EVAL-02",
        "severity_hint": "SUGGESTION",
        "citation": "Genie Promotion Handbook › Quality › EVAL-02",
        "content": (
            "Instruções vagas ('responda qualquer coisa') induzem joins alucinados e números "
            "errados. Prefira instruções específicas ancoradas nas tabelas Diamond."
        ),
        # Space "instructions" steer a natural-language answer; a dashboard renders fixed widgets and
        # has no equivalent surface to be vague on.
        "kinds": _GENIE_ONLY,
    },
    {
        "rule_id": "SQL-01",
        "severity_hint": "STYLE",
        "citation": "Genie Promotion Handbook › SQL › SQL-01",
        "content": (
            "SQL de exemplo deve seguir convenções Acme: palavras-chave em MAIÚSCULAS, "
            "aliases com AS, preferir tabelas Diamond, evitar SELECT *."
        ),
    },
]

# The dashboard structural rules live in `dashboard_check` (next to the code that enforces them, so a
# rule and its implementation can't drift) and are merged in here, each scoped to the dashboard kind,
# so `RULES` remains the single rule inventory the admin Regras UI and `rules_config` read.
_DASHBOARD_RULES = [{**rule, "kinds": (resource_kind.DASHBOARD,)} for rule in dashboard_check.RULES]

RULES = _BASE_RULES + _DASHBOARD_RULES


def rules_for_kind(kind: str | None = None) -> list[dict]:
    """The hardcoded rules that apply to one resource kind (a rule with no ``kinds`` applies to all).

    ``kind=None`` returns EVERY rule — the inventory view the admin console needs, and the historical
    meaning of `RULES` for any caller that predates kind scoping.
    """
    if kind is None:
        return [dict(rule) for rule in RULES]
    return [dict(rule) for rule in RULES if kind in (rule.get("kinds") or (kind,))]

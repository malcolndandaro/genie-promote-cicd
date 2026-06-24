"""The Genie Promotion Handbook as structured rules (S5).

The handbook is small (9 rules), so the agent grounds on ALL of them in-context and
cites a rule_id per finding — no Vector Search needed (that's the scale path, kept as
a future option). Source of truth: handbook/genie-handbook.md. Keep in sync.
"""

RULES = [
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
        "rule_id": "GRANT-01",
        "severity_hint": "BLOCKER",
        "citation": "Genie Promotion Handbook › Access › GRANT-01",
        "content": (
            "Toda tabela em data_sources deve ser legível (SELECT) pelo grupo consumidor "
            "de produção. Espaço cujas fontes o grupo não pode ler é inutilizável — a falha "
            "clássica do Acme. BLOCKER. (Verificação determinística de grants alimenta esta regra.)"
        ),
    },
    {
        "rule_id": "GRANT-02",
        "severity_hint": "SUGGESTION",
        "citation": "Genie Promotion Handbook › Access › GRANT-02",
        "content": "O dono/CAN_MANAGE do espaço de produção deve ser um grupo, nunca um indivíduo.",
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
            "Um espaço de produção deve ter >= 3 perguntas de benchmark (Q->SQL). Sem elas "
            "não há o que certificar pelo eval-run. BLOCKER."
        ),
    },
    {
        "rule_id": "EVAL-02",
        "severity_hint": "SUGGESTION",
        "citation": "Genie Promotion Handbook › Quality › EVAL-02",
        "content": (
            "Instruções vagas ('responda qualquer coisa') induzem joins alucinados e números "
            "errados. Prefira instruções específicas ancoradas nas tabelas Diamond."
        ),
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

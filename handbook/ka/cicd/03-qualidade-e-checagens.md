# Qualidade & Checagens Determinísticas (regras do handbook)

Guidance GERAL sobre as regras de qualidade e as checagens determinísticas que gatekeepeiam uma
promoção. Independente de domínio.

## Regras determinísticas (aplicadas por código, não pelo LLM)

- **ENV-01 (BLOCKER)** — um espaço de produção referencia apenas catálogos `prod_<domínio>`.
  Qualquer referência a `dev_`/`sbx_`/outro ambiente é bloqueador. Verificada no CI (pré-render +
  allowlist), independente do texto — desabilitar no app não desliga o gate.
- **AUDIENCE-01** — valida os principais e tabelas do Público do Space. Falta de SELECT é advisory
  e orienta o processo Terraform da CERC; entrada inválida bloqueia e falha de inspeção é operacional.
- **EVAL-01 (BLOCKER, configurável)** — o espaço tem ≥ N perguntas de benchmark (default 2). Contada
  deterministicamente. O mínimo e o limiar do eval-run são configuráveis pelo admin.

## Regras avaliadas pelo revisor (LLM, texto configurável)

- **ENV-02 (SUGGESTION)** — o warehouse do espaço deve ser o de produção.
- **PII-01 (BLOCKER)** — coluna de PII de pessoa física / sigilo bancário exposta sem máscara a
  grupo não autorizado. Identificador de empresa (CNPJ) não é bloqueador por si só.
- **PII-02 (SUGGESTION)** — instruções não devem induzir cruzamento entre clientes ou vazamento
  entre tenants.
- **EVAL-02 (SUGGESTION)** — instruções vagas induzem joins alucinados; prefira instruções
  específicas ancoradas nas tabelas gold.
- **SQL-01 (STYLE)** — SQL de exemplo em MAIÚSCULAS nas keywords, aliases com AS, preferir tabelas
  gold, evitar `SELECT *`.

## Boas práticas de benchmark

- Adicione perguntas de benchmark (Q→SQL) representativas do uso real do espaço na aba Benchmarks.
- Cada pergunta deve ter uma consulta de referência correta, para o eval-run medir a taxa de acerto.
- Um limiar de eval-run (pass-rate) razoável é ~80%; abaixo disso, revise instruções e exemplos.

## Certificação de dados (contexto)

- A API de Genie Space não expõe um campo de "certificação" do próprio espaço. A confiança vem das
  tabelas de Unity Catalog que ele consulta — certifique/tagueie os assets UC (tabelas gold), não o
  espaço. Um espaço herda a confiabilidade das tabelas gold que referencia.

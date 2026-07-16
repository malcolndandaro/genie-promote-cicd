# Convenções de Modelagem — Recebíveis (CERC)

Padrões de modelagem e nomenclatura esperados nas tabelas e nos Genie Spaces do domínio de
recebíveis. Um espaço promovido deve ser coerente com estas convenções.

## Nomenclatura de tabelas (camada diamond / gold)

- **Fatos**: prefixo `fato_` — ex.: `fato_recebiveis`. Grão sempre documentado (um registro por
  recebível).
- **Dimensões**: prefixo `dim_` — ex.: `dim_cedente`, `dim_arranjo`.
- Catálogo por ambiente: `dev_recebiveis` em dev, `prod_recebiveis` em produção. Um espaço de
  produção jamais referencia catálogo de outro ambiente (`dev_`, `sbx_`).
- Schema de gold: `diamond`. Análises de negócio consomem `prod_recebiveis.diamond.*`.

## Convenções de SQL (exemplos e benchmarks do espaço)

- Palavras-chave SQL em MAIÚSCULAS (`SELECT`, `FROM`, `JOIN`, `GROUP BY`).
- Identificadores (tabelas, colunas) em minúsculas com `snake_case`.
- Usar aliases explícitos com `AS`.
- Preferir as tabelas diamond; evitar `SELECT *` em exemplos — liste as colunas.
- Joins entre fato e dimensões pela chave natural documentada (ex.: `cnpj_cedente`, `id_arranjo`).

## Qualidade do Genie Space

- Um espaço de produção deve ter **≥ 2 perguntas de benchmark** (Q→SQL) na aba Benchmarks — sem
  elas não há como certificar a qualidade das respostas via eval-run.
- Instruções vagas ("responda qualquer coisa") induzem joins alucinados e números errados. Prefira
  instruções específicas ancoradas nas tabelas diamond e nas métricas definidas no glossário.
- As instruções devem refletir as definições de métrica do glossário do domínio (volume de
  recebíveis, valor antecipado) para respostas consistentes.

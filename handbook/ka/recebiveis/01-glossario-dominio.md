# Glossário do Domínio — Recebíveis (CERC)

Este documento define os termos de negócio do domínio de **recebíveis de cartão** usado nos
Genie Spaces de produção. Um Genie Space promovido para produção deve usar estes conceitos de forma
consistente com as definições abaixo.

## Conceitos centrais

- **Recebível**: direito de crédito de um estabelecimento comercial (cedente) a receber de uma
  transação de cartão já capturada, antes da liquidação. É o ativo central do domínio.
- **Cedente**: o estabelecimento comercial (pessoa jurídica) titular dos recebíveis. Identificado
  por **CNPJ** (identificador de empresa — dado de negócio, NÃO é PII de pessoa física).
- **Arranjo de pagamento**: o esquema/bandeira sob o qual a transação foi feita (ex.: Visa,
  Mastercard, Elo). Modelado na dimensão `dim_arranjo`.
- **Registradora**: entidade autorizada pelo Banco Central a registrar recebíveis (a CERC é uma
  registradora). O registro de recebíveis é regulado pela **Resolução BCB nº 264** e correlatas.
- **Antecipação**: operação em que o cedente recebe o valor do recebível antes da data de
  liquidação, mediante deságio. Espaços sobre "Recebíveis Antecipados" tratam desse fluxo.

## Camada Diamond (gold) — tabelas autoritativas

Os Genie Spaces de produção devem consultar SOMENTE a camada **diamond** (gold) do catálogo
`prod_recebiveis`. As tabelas canônicas:

- `prod_recebiveis.diamond.fato_recebiveis` — fato central: um registro por recebível, com valor,
  data de liquidação, cedente, arranjo. Grão: recebível.
- `prod_recebiveis.diamond.dim_cedente` — dimensão de cedentes (CNPJ, razão social, situação).
- `prod_recebiveis.diamond.dim_arranjo` — dimensão de arranjos de pagamento (bandeira, tipo).

Nunca referenciar camadas bronze/silver diretamente em um espaço de produção — o consumo de negócio
é sempre pela diamond, que já aplica as regras de qualidade e conformidade.

## Métricas comuns (definições consistentes)

- **Volume de recebíveis**: soma de `valor` em `fato_recebiveis`, geralmente agrupada por bandeira
  (`dim_arranjo`) ou por período de liquidação.
- **Valor antecipado**: valor de recebíveis com antecipação aplicada (deságio já descontado).
- Ao responder perguntas de volume, prefira agrupar por dimensão de negócio (bandeira, cedente,
  período) e sempre deixar claro o período/filtro aplicado.

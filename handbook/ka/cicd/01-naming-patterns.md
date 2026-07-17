# Padrões de Nomenclatura — Promoção de Genie Spaces & AI/BI

Guidance GERAL de CI/CD (independente de domínio) para promover Genie Spaces e dashboards AI/BI de
dev para produção com um pipeline governado. Aplica-se a qualquer domínio, não só recebíveis.

## Catálogo por ambiente (catalog-per-env)

- Padrão: `<env>_<domínio>` — ex.: `dev_recebiveis` (dev), `prod_recebiveis` (produção).
- Um artefato de produção deve referenciar **apenas** catálogos `prod_<domínio>`. Qualquer
  referência a `dev_`, `sbx_` ou outro ambiente em um espaço de produção é um erro **bloqueador**
  (regra ENV-01). O pipeline reescreve `dev_ → prod_` no render e rejeita qualquer referência
  cruzada de ambiente.
- Nunca hardcodar o nome do workspace, warehouse id, catálogo ou id de espaço no código — tudo é
  configurável por ambiente (portável por construção).

## Nomenclatura de recursos

- **Schemas medallion**: `bronze` (raw), `silver` (conformado), `gold`/`diamond` (consumo). Genie
  Spaces de produção consomem a camada gold/diamond.
- **Fatos**: `fato_<assunto>`; **dimensões**: `dim_<assunto>`. `snake_case`, minúsculas.
- **Branch de promoção**: `promote/<slug>` — um branch por espaço, para que promoções concorrentes
  não colidam.
- **Arquivo do artefato**: `src/genie/<slug>.serialized_space.json`, com sidecars `<slug>.title`,
  `<slug>.audience.json`, `<slug>.mapping.json` e `<slug>.revision.json` ao lado.

## Slug do espaço

- O slug identifica o espaço no repositório de conteúdo e no recurso de produção. Prefira um slug
  amigável e estável (ex.: `receivables`) fixado por configuração, em vez de derivar do id longo do
  espaço — assim o espaço mantém o mesmo arquivo/recurso de produção entre promoções.

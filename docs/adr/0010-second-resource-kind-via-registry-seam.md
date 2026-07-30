# ADR-0010 — Um segundo tipo de recurso (painéis AI/BI) via seam de registry

- **Status:** aceito
- **Data:** 2026-07-30
- **Supersede:** nada. Estende ADR-0003 (pré-render), ADR-0007 (deploy seguro) e ADR-0009 (modelo de
  acesso) para um segundo tipo de recurso.

## Contexto

O acelerador promovia **Genie Spaces**. Painéis AI/BI (Lakeview) já eram *deployáveis* — havia um
bloco fixo no `scripts/render.sh` com um nome de arquivo, uma chave de recurso e um display name
hardcoded — mas não eram **promovíveis nem governados**: sem slug, sem sidecars, sem Promotion,
sem auditoria, sem certificação, e invisíveis a todos os gates (o cálculo de slugs alterados só
casava `^src/genie/`).

Um segundo tipo de recurso podia ser adicionado de duas formas: ramificando por tipo em cada camada
(`app_logic`, `render.sh`, `deploy_attempt`, a SPA), ou concentrando o que varia em um lugar.

## Decisão

**Um registry congelado de fatos por tipo** (`genie_reviewer/resource_kind.py`) mais **um adaptador
de SDK por tipo** (`genie_reviewer/workspace_resource.py`). Todo o resto do pipeline — Promotion,
Change Request, PR em rascunho, gate de Environment, state machine de deploy, trilha de auditoria —
é reutilizado sem ramificação.

A barra de aceite foi **comportamento de `genie_space` byte-idêntico**. Adicionar um terceiro tipo é
uma entrada no registry mais seus adaptadores, não uma varredura pelo código.

### O que varia, e por quê

| Concern | Genie Space | Painel AI/BI |
|---|---|---|
| Export | `serialized_space` | `serialized_dashboard` (também string JSON) |
| Artefato | `<slug>.serialized_space.json` | `dashboard.lvdash.json` |
| Layout | FLAT (`src/genie/<slug>.*`) | ANINHADO (`src/dashboards/<área>/<nome>/`) |
| Objeto de permissão | `genie` | `dashboards` (**plural**) |
| Nível de audiência | `CAN_RUN` | `CAN_READ` |
| Entity type de tag | `geniespaces` | `dashboards` |
| Piso de qualidade | benchmarks (EVAL-01) + eval-run | DASH-01..04 + DASH-SQL |
| Coleção DABs | `genie_spaces` | `dashboards` |

Todo valor de plataforma acima foi **verificado ao vivo**, não inferido — a ADR-0007 é explícita que
um exemplo genérico de DABs não é evidência do suporte de ACL/tag de um recurso. O singular
`dashboard` é rejeitado pela API de permissões; `dashboard`/`lakeviewdashboards`/`aibidashboards` são
rejeitados pela API de tags; `dbsql-dashboards` é outro objeto (Redash) que este acelerador não
promove.

## Decisões que não são óbvias

### 1. O scan de catálogo de um painel é ESTRUTURAL, e é um denylist de prosa

ENV-01 varre tudo **exceto** os widgets de texto/markdown (`pre_render.dashboard_sql_text` remove
qualquer `*TextboxSpec` e escaneia o resto).

Duas descobertas ao vivo, em painéis reais de dev, forçaram esse desenho:

- Um link markdown fez a gramática de referência de 3 partes casar **`en.wikipedia.org`** e reportar
  o catálogo `en` como estrangeiro → **BLOCKER falso** num painel perfeitamente válido.
- O caso inverso: um heading `# cerc_mlops_dev_catalog.inference.inference_scores Monitoring` — uma
  referência de catálogo dev legítima, em **prosa**.

A assimetria é a decisão: **SQL bloqueia, prosa avisa**. Nenhuma query roda de um widget de texto,
então uma referência remanescente ali é defeito de documentação; o rebind do documento inteiro a
reescreve e a DASH-04 a reporta como advisory.

A primeira implementação era um **allowlist** de `datasets[].queryLines`, e isso era um buraco:
`parameters[].defaultSelection` é texto livre que alcança o engine via `IDENTIFIER(:param)`, e
`asset_name` referencia uma tabela UC **sem SQL nenhum**. Um allowlist de campos conhecidos vaza a
cada evolução do schema `.lvdash.json`; um denylist do único construto que genuinamente não é dado
falha na direção **segura** — um campo novo é escaneado por padrão.

### 2. Um painel não tem benchmarks, então o eval é SUBSTITUÍDO, não degradado

EVAL-01/eval-run não se aplicam. Em vez de reportá-los como "advisory/indisponível" (o que nomearia
uma checagem que nunca pode rodar), o piso de qualidade é:

- **DASH-01..04** — integridade estrutural, offline: widget apontando para dataset inexistente
  (BLOCKER), dataset órfão (advisory), painel vazio (BLOCKER), prosa com catálogo estrangeiro
  (advisory);
- **DASH-SQL** — `EXPLAIN` de cada dataset renderizado contra o warehouse de **produção**. Pega
  exatamente a falha que o pipeline existe para evitar: "funcionava em dev".

### 3. Layout aninhado por área de negócio, sem diretório de versão

`src/dashboards/<área>/<nome>/` com sidecars de nome fixo. A área é a **área de negócio dona**, que é
como um autor de negócio procura o painel — não o domínio de dados.

Não há diretório de versão: o **git já guarda o histórico**, então uma revisão nova substitui os
mesmos arquivos e o diff mostra o que mudou. Genie permanece FLAT de propósito — 7 Spaces já estão
promovidos assim, e movê-los reescreveria branches de promoção vivas sem ganho visível.

A área é **vocabulário controlado** (`genie_reviewer/business_area.py`, configurável por
`APP_BUSINESS_AREAS`), validado no picker da UI, no engine e no CI de conteúdo. Texto livre
fragmentaria a mesma área em `risco`/`Risco`/`risk`; e como o valor vira diretório, segmento de
branch e parte da chave de recurso DABs, o conjunto fechado é **também** a defesa contra path
traversal. Uma área desconhecida é recusada — não existe bucket "outros".

### 4. Painéis têm página própria na UI

"Painéis AI/BI" (`#/paineis`) é destino próprio; "Meus espaços" volta a ser só Genie. Uma lista
compartilhada impunha vocabulário de Genie a um painel ("espaço", benchmarks, `CAN_RUN`) e não deixava
espaço para o que um painel de fato é: datasets, widgets, páginas, e a área onde é arquivado.

### 5. Publicação mantém `embed_credentials=false`

O painel publicado roda as queries **como quem o abre**, então o consumidor continua precisando do
próprio SELECT no Unity Catalog. Promoção concede **visibilidade, nunca dado** — mudar isso
transformaria o pipeline em mecanismo de acesso a dados, exatamente o que a ADR-0009 retirou.

## Consequências

- Sem migração de Lakebase: `promotions.resource_kind` já existia.
- Os **nomes dos estágios** de deploy não mudaram (são contrato persistido em
  `deployment_attempts.completed_stages` e renderizados no app). Cada estágio itera os artefatos de
  todos os tipos; uma promoção só-de-painel é válida, e uma árvore de conteúdo genuinamente vazia
  continua falhando fechado.
- Um slug deve identificar exatamente um recurso **entre todos os tipos**. Prefixos (`s_`/`d_`) só
  garantem isso para slugs GERADOS; um slug amigável fixado não tem prefixo, então uma colisão é
  alcançável por configuração e falha alto.
- `/api/resources` devolve um DTO discriminado; `/spaces`, `prod_space_id` e o campo `space_id` de
  request seguem como aliases de compatibilidade.
- A chave de recurso DABs de um painel é **achatada** (`<área>__<nome>`) enquanto o `file_path`
  mantém o diretório real. `resource_kind.resource_key` é a única fonte desse mapeamento.

## Lições do primeiro deploy real

Três defeitos passaram por 750 testes verdes e apareceram no primeiro deploy em produção. O padrão é
mais útil que os bugs:

1. **Um gate lendo o caminho de arquivo errado** ficava verde validando **zero**. Os testes cobriam o
   *conteúdo* do gate, nunca que ele **encontra o arquivo**. Um passo de CI que monta caminho merece
   teste tanto quanto lógica.
2. **Renomear uma chave de recurso DABs lê como delete+create.** O `bundle deploy` recusou sem
   `--auto-approve` — a recusa está correta, porque recriar um painel muda seu id e sua URL
   permanente. A resposta foi um flag **opt-in por execução** (`--allow-destructive`), nunca um
   default: um rename futuro deve falhar fechado, não destruir em silêncio.
3. **Resolução por título aceitava um recurso recém-deletado.** A listagem é eventualmente
   consistente, então após um recreate o tombstone aparece como um match único e limpo, e os estágios
   seguintes miram um objeto morto. Um match único agora é **sondado** antes de ser aceito.

A conclusão operacional: suíte verde não é evidência de que algo funciona ponta a ponta. Cada um
desses só apareceu ao executar o caminho real contra produção.

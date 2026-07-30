# Instruções do repositório

Estas instruções valem para todo o repositório `genie-promote-cicd`. Elas foram escritas tanto para
pessoas contribuidoras quanto para agentes de código. O `AGENTS.md` é a cópia canônica; o `CLAUDE.md`
aponta para cá para que os dois arquivos não divirjam.

Este é um acelerador portável e reutilizável. O repositório não inclui, no momento, uma licença de
software, então não o descreva como open source nem o redistribua como tal até que uma licença seja
adicionada. Não herde nomes de workspace, identificadores, credenciais, URLs, domínios ou premissas
operacionais específicos de um cliente vindos de um checkout pai ou de um documento de handoff antigo.
Use o código e a documentação locais do repositório como fonte de verdade.

## Entenda o produto antes de mudá-lo

O Genie Promote é um caminho de entrega governado para recursos do Databricks — hoje **Genie Spaces** e
**painéis AI/BI (Lakeview)**. O **Autor** trabalha
em um workspace de DEV e clica em **"Preparar promoção"** no app (hospedado em PROD). O app, via service
principal (SP), abre um **PR em rascunho** no repositório de conteúdo, onde os checks de CI rodam como
um dry-run do Autor. O **Responsável Técnico** revisa o rascunho no GitHub, marca como pronto (*ready*)
e faz o merge — esse é o ato de promover. A **Plataforma** aprova o deploy de produção pelo gate do
Environment protegido.

Toda a separação de funções (SoD) é imposta pelo **GitHub**: a permissão `Write` decide quem faz merge;
o required-reviewer do Environment `prod` decide quem aprova o deploy. O app **não** decide quem promove
nem quem aprova — ele guarda um único bit, `admin` ("Administrador da Plataforma"), que só libera o
console de administração do próprio app. O SP **nunca** marca um PR como pronto; ele apenas abre ou
rebaixa para rascunho.

Leia estes arquivos nesta ordem:

1. `README.md` — propósito do produto, arquitetura, fronteiras de confiança e domínio dos repositórios.
2. `SETUP.md` — runbook canônico de instalação e operação.
3. ADRs relevantes em `docs/adr/` — decisões de design e trade-offs.
4. `docs/security/assert-can-access-threat-model.md` — a fronteira de autorização entre workspaces.

Se código, comportamento de workflow e documentação divergirem, verifique a implementação e atualize a
documentação na mesma mudança. Não preserve uma afirmação desatualizada por compatibilidade histórica.

## Mantenha os dois repositórios separados

Este é o **repositório do engine**. Ele é dono de:

- o Databricks App, o backend e o frontend em Svelte;
- a lógica de revisão, autorização, política, avaliação e rehydrate;
- os scripts de render, validação, deploy e provisionamento;
- a definição do bundle Databricks e os testes automatizados.

O **repositório de conteúdo** que o acompanha é dono de:

- Genie Spaces serializados e seus sidecars de título, audiência e mapeamento;
- painéis AI/BI em layout ANINHADO por área de negócio — `src/dashboards/<área>/<nome>/` com
  `dashboard.lvdash.json` + sidecars de nome FIXO (`title` obrigatório e não vazio, `audience.json`
  obrigatório, `mapping.json` opcional, `revision.json`). Sem diretório de versão: o git já guarda o
  histórico, então uma revisão nova SUBSTITUI os mesmos arquivos e o diff mostra o que mudou;
- conteúdo opcional de setup;
- o `engine.lock` e os workflows de promoção, checks e deploy.

Coloque cada mudança no repositório que é dono dela. Não adicione conteúdo promovido ao repositório do
engine, nem mova lógica do engine para o repositório de conteúdo. Ao testar os dois repositórios juntos,
fixe o commit exato e revisado do engine no `engine.lock` do repositório de conteúdo.

## Fronteiras de deploy não-negociáveis

- Trate o `SETUP.md` como a autoridade no nível dos comandos. As amostras neste repositório são
  exemplos, não valores padrão de produção.
- Use perfis explícitos da CLI do Databricks e valores explícitos de DEV/PROD. Nunca dependa do perfil
  padrão implícito de um operador para uma mutação de workspace.
- Mantenha o app em PROD e use o service principal de transporte para DEV somente **depois** que o app
  verificar o acesso vivo do humano autenticado ao Genie Space em DEV. Uma identidade de transporte não
  é autorização.
- Mantenha separadas as identidades de validação de PR e de deploy de produção. O SP de validação deve
  ser não-admin e voltado para leitura. O SP de deploy pode ter a autoridade administrativa —
  estritamente documentada — exigida para o binding App/Lakebase.
- Guarde as variáveis `DATABRICKS_PROD_*` apenas no Environment `prod` protegido do repositório de
  conteúdo. Os jobs de PR com escopo de repositório usam `DATABRICKS_VALIDATION_*`; a avaliação com DEV
  vivo usa `DATABRICKS_DEV_*`.
- Rode workflows credenciados apenas para contribuições confiáveis, em runners isolados. Não execute
  código arbitrário de forks públicos em um runner persistente que guarda credenciais de workspace.
- O deploy reconcilia as permissões de audiência do Genie. Ele **não** concede acesso a tabelas do Unity
  Catalog; a governança dos dados do cliente permanece externa a este acelerador.
- `lakebase_direct_access_admins` documenta uma allowlist pretendida, mas não impõe ACLs do projeto
  Lakebase nem grants do Postgres. Nunca descreva mudar essa variável como uma operação de controle de
  acesso.
- Nunca rode um deploy completo do bundle a partir de um checkout só-do-engine depois que o conteúdo de
  PROD passa a ser gerenciado. Um `src/` vazio do engine pode ser interpretado como o estado desejado
  (vazio) e apagar o conteúdo gerenciado. Todo deploy de regime permanente deve sobrepor o repositório
  de conteúdo completo, como descrito no `SETUP.md`.
- Agentes automatizados não devem fazer deploy, conceder permissões, rotacionar credenciais, configurar
  o GitHub, fazer merge, push ou commit a menos que a pessoa usuária peça explicitamente essa ação
  externa ou persistente. Pessoas contribuidoras devem seguir o processo normal de revisão e release do
  repositório.

## Faça mudanças portáveis

- Mantenha código, documentação portável e exemplos voltados a quem desenvolve em inglês, a menos que
  uma tarefa peça localização explicitamente. O app existente tem telas em português: preserve o idioma
  da superfície que você está mudando e não misture idiomas dentro de um mesmo fluxo. Novas superfícies
  reutilizáveis do produto devem, por padrão, ficar em inglês, salvo se a tarefa mirar um locale
  específico.
- Use placeholders neutros de cliente, como `<owner>`, `<domain>`, `<prod-profile>` e
  `<prod-workspace-url>`, na documentação reutilizável.
- Trate o domínio `recebiveis` versionado, as identidades, e-mails, nomes de repositório, IDs de Space e
  nomes de endpoint como dados de amostra. Não os introduza como valores padrão genéricos.
- Nunca faça commit de segredos, credenciais OAuth, PATs, chaves privadas, installation IDs reais ou
  manifestos gerados que carreguem segredos.
- Preserve mudanças não relacionadas de outras pessoas. Inspecione `git status` e o diff relevante antes
  de editar.
- Use a documentação atual do Databricks para comportamento sensível a versão de CLI, bundle, Apps,
  Lakebase, Genie e permissões.

## Regras de edição

- Python: siga a PEP 8, adicione type hints, use f-strings e coloque docstrings em funções públicas.
- Svelte/TypeScript: siga os padrões existentes de componente e de store; mantenha os erros visíveis ao
  usuário acionáveis e preserve a acessibilidade.
- SQL: palavras-chave em MAIÚSCULAS e identificadores em minúsculas.
- Prefira objetos de request tipados do SDK do Databricks a dicionários crus onde o SDK os exigir.
- Preserve o contrato protegido do revisor em `genie_reviewer/review_core.py`: o conteúdo não-confiável
  de um Space permanece **dado** (nunca instrução), e o schema da resposta permanece imposto. O texto
  editável de persona do revisor não pode substituir a defesa protegida contra prompt injection nem o
  contrato do parser.
- Não edite à mão arquivos gerados sob `build/`. Altere os templates ou scripts de origem e regenere-os
  com `scripts/render.sh` ou `scripts/build_promote_app.sh`.
- Mantenha estáveis os nomes públicos dos jobs de workflow quando a branch protection depender deles, em
  especial `bundle validate (prod)` e `eval-run pass-rate (dev)`.
- A configuração dos workflows deve falhar fechada (*fail closed*). Credenciais ausentes, IDs de Space
  não resolvidos ou inputs obrigatórios faltando devem fazer um job obrigatório falhar — nunca pular
  nem retornar um sucesso consultivo.

## Tipos de recurso (o *kind seam*)

O pipeline é agnóstico ao tipo de recurso. Tudo que varia entre um Genie Space e um painel AI/BI é um
valor em `genie_reviewer/resource_kind.py` (diretórios, sufixo do artefato, prefixo de slug, tipo de
objeto de permissão, entity type de tag, nível de audiência, `has_benchmarks`). As chamadas ao SDK que
variam ficam em `genie_reviewer/workspace_resource.py`. Para adicionar um terceiro tipo: uma entrada no
registry + seus adaptadores — **não** novos ramos espalhados por `app_logic`/`render.sh`/`deploy_attempt`.

Regras que não são óbvias e já custaram caro:

- **O scan de catálogo de um painel é ESTRUTURAL, e implementado como DENYLIST de prosa.**
  `pre_render.dashboard_sql_text` remove as subárvores `*TextboxSpec` e devolve TODO o resto do
  documento para o ENV-01 varrer. Uma varredura do documento inteiro dá falso positivo: um link
  markdown fez a gramática de referência de 3 partes casar `en.wikipedia.org` e reportar o catálogo
  `en` como BLOCKER. Um catálogo citado em **prosa** é reescrito pelo rebind e reportado como DASH-04
  **consultivo** — prosa não é caminho de dados, nenhuma query roda de um widget de texto.
  **Não troque por allowlist.** A primeira versão concatenava apenas `datasets[].queryLines` e era um
  buraco real: `parameters[].defaultSelection` é texto livre que alcança o engine via
  `IDENTIFIER(:param)` (verificado ao vivo) e `asset_name` referencia tabela do UC sem SQL algum. Uma
  allowlist volta a vazar a cada campo novo do schema; a denylist falha na direção segura, porque um
  campo desconhecido é varrido por padrão.
- **Um painel não tem benchmarks.** EVAL-01/eval-run não se aplicam e não devem ser degradados para
  "advisory": o piso de qualidade é DASH-01..04 (estrutural, offline) + `check_dashboard_sql.py`
  (`EXPLAIN` de cada dataset contra o warehouse de PRODUÇÃO).
- **Valores da plataforma verificados ao vivo** (ADR-0007: um exemplo genérico de DABs não é evidência):
  tipo de objeto de permissão `dashboards` (plural — o singular é rejeitado, e `dbsql-dashboards` é
  outro objeto); entity type de tag `dashboards`; níveis `CAN_READ`/`CAN_RUN`/`CAN_EDIT`/`CAN_MANAGE`.
- **`.title` é obrigatório e não vazio para os dois tipos** — vira `display_name` e é a única chave de
  resolução do id no deploy (`bundle summary` não devolve id de Space nem de painel). Falta de título
  falha no render, nunca no meio da mutação.
- **Os nomes dos estágios de deploy são contrato persistido** (`deployment_attempts.completed_stages`).
  Um novo tipo de recurso **itera dentro** dos estágios; não acrescenta estágio.
- **A área de negócio é vocabulário CONTROLADO** (`genie_reviewer/business_area.py`, configurável via
  `APP_BUSINESS_AREAS`). Ela vira diretório, segmento de branch e parte da chave de recurso DABs, então
  é validada no app, no engine e no CI — texto livre fragmentaria a mesma área em `risco`/`Risco`/`risk`
  e também abriria path traversal. Uma área desconhecida é REFUSA, nunca um bucket "outros".
- **A chave DABs de um painel é achatada** (`<área>__<nome>`), mas o `file_path` mantém o diretório
  real. As duas coisas divergirem quebra o deploy — `resource_kind.resource_key` é a única fonte.
- **Publicação de painel usa `embed_credentials=false`.** O painel publicado roda como quem o abre, então
  o consumidor continua precisando do próprio SELECT no Unity Catalog. Mudar isso transformaria o
  pipeline em mecanismo de acesso a dados — exatamente o que a ADR-0009 retirou.
- **Um slug identifica UM recurso entre TODOS os tipos.** Os prefixos (`s_`/`d_`) garantem isso apenas
  para slugs GERADOS — um slug amigável fixado não tem prefixo, então uma colisão é alcançável por
  configuração. `_all_artifacts` recusa alto: sem isso, `resolve_space` e `_kind_of` discordam e um
  estágio aplica o objeto/tag de Genie ao id de um painel.
- **O slug é IDENTIDADE; a área e o título são DECLARAÇÕES.** O slug aninhado é derivado de (área,
  título), então re-promover o mesmo recurso com um dos dois alterado derivaria um slug DIFERENTE e
  bifurcaria um SEGUNDO diretório governado para um único recurso. Um recurso já promovido mantém o
  slug da primeira promoção (`app_logic.prior_slug_for`, recuperado do branch persistido); o título
  novo continua chegando à produção pelo sidecar, que é apresentação. Mover de área de verdade é um PR
  deliberado, porque a chave DABs vem do slug e renomeá-la lê como delete+create.
- **Um título identifica UM recurso dentro do seu tipo, no estado DESEJADO também.** O título é a única
  chave de resolução de id, então dois slugs com o mesmo título fazem o `bundle deploy` tentar CRIAR um
  recurso cujo display name já existe (409 `ALREADY_EXISTS`) — no meio da mutação. O `preflight` recusa
  antes de qualquer mutação. Tipos diferentes PODEM compartilhar título: são objetos distintos.

Ver a [ADR-0010](docs/adr/0010-second-resource-kind-via-registry-seam.md) para o desenho completo.

## Deploy: o que o primeiro deploy real ensinou (leia antes de mexer no pipeline)

Três defeitos passaram por 750 testes verdes e só apareceram em produção. O padrão vale mais que os
bugs, então está aqui e não só no histórico do git:

- **Um passo de CI que MONTA um caminho de arquivo merece teste como lógica.** O gate de painéis
  montava o caminho flat antigo; com slug aninhado o arquivo não existia, todo gate caía no `skip`, e o
  required check ficava **verde validando zero**. Os testes cobriam o *conteúdo* do gate, nunca que ele
  **encontra o arquivo**. Um gate que não acha seu input deve FALHAR, nunca continuar.
- **Renomear uma chave de recurso DABs lê como delete+create.** O `bundle deploy` recusa sem
  `--auto-approve`, e a recusa está CORRETA: recriar um painel muda seu id e sua URL permanente. Existe
  `--allow-destructive` (também `ALLOW_DESTRUCTIVE_DEPLOY=1`), **desligado por padrão** e pensado como
  opt-in por execução. Nunca o torne default nem o deixe fixo num workflow — habilite, rode, desabilite.
- **Resolução por título pode devolver um recurso recém-deletado.** A listagem é eventualmente
  consistente, então após um recreate o tombstone aparece como um match único e limpo e os estágios
  seguintes miram um objeto morto. `resolve_by_title` SONDA o candidato com um get antes de aceitá-lo;
  não remova essa sonda.

Corolário para verificação: **suíte verde não é evidência de que algo funciona ponta a ponta**. Ao
tocar render, gates ou deploy, confirme o estado vivo pela API — não pelo status do run, que já ficou
verde escondendo um painel que nunca foi implantado.

## Dono da documentação

- `README.md` explica o que é o produto, como as peças se encaixam e por onde começar.
- `SETUP.md` é o único runbook canônico de deploy e operação.
- `AGENTS.md` registra as restrições para contribuidores; não deve virar um segundo guia de setup.
- `CLAUDE.md` deve permanecer um ponteiro curto para este arquivo.

Quando o comportamento de deploy mudar, atualize o `SETUP.md` e qualquer visão geral afetada no README
na mesma mudança. Verifique os comandos contra os scripts e workflows atuais. Mantenha o histórico de
instalação de clientes fora destes documentos portáveis.

## Verifique proporcionalmente

Para mudanças de backend ou do engine compartilhado:

```bash
python3 -m pytest tests/ -q
```

Para mudanças de frontend:

```bash
(
  cd web
  npm ci
  npm run check
  npm run build
)
```

Para mudanças de render ou de bundle:

```bash
bash scripts/render.sh prod
```

Quando houver credenciais de teste válidas e a validação com ambiente vivo estiver no escopo, rode
também o comando estrito de validação do bundle documentado no `README.md`/`SETUP.md`. A validação pode
contatar o Databricks; não presuma que é um check offline.

Para checar a prontidão entre repositórios sem mutar o workspace:

```bash
python3 scripts/pilot_readiness.py \
  --content-repo /caminho/para/genie-spaces-content \
  --offline-only
```

Rode testes focados enquanto itera e, então, os checks completos apropriados antes de entregar. Rode
também `git diff --check` e revise o diff final em busca de arquivos gerados por acidente, segredos e
valores específicos de cliente. Relate exatamente o que rodou e o que não pôde rodar.

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

O Genie Promote é um caminho de entrega governado para Genie Spaces do Databricks. O **Autor** trabalha
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
- conteúdo opcional de dashboards e setup;
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

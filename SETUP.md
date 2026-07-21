# Guia de instalação e operação

Bem-vindo(a)! Este é o guia oficial para colocar o **Genie Promote** no ar em um novo par de
workspaces Databricks (DEV/PROD) e em um novo owner do GitHub.

Não se assuste com o tamanho: a instalação tem várias partes, mas o caminho é linear. Cada etapa
abaixo termina com um **resultado visível**, então você sempre sabe onde está. Faça uma etapa de
cada vez, do começo ao fim, e no final você terá um ambiente completo e governado.

> 💡 **Como ler este guia.** Comandos, nomes de variáveis e nomes de arquivos aparecem
> exatamente como no código (`databricks bundle deploy`, `APP_ADMINS`, `pr-checks.yml`) — não
> traduza esses. Onde você vê algo entre `<colchetes>`, substitua pelo seu valor. Não é um
> instalador de um comando só: é um passo a passo que você acompanha.

## O que você terá no final

Ao concluir este guia, o ambiente terá:

- **um App Databricks hospedado em PROD** com seu banco de estado Lakebase;
- **um service principal (SP) de transporte DEV**, para operações Genie entre workspaces com
  verificação de acesso;
- **um SP de validação em PROD, de menor privilégio**, usado só pelos checks de PR;
- **um SP de deploy em PROD (admin de workspace)**, disponível apenas atrás do gate de produção;
- **um GitHub App** para abrir pull requests de conteúdo e ler status;
- **dois repositórios**: o do engine e o de conteúdo;
- **checks protegidos no repositório de conteúdo** e um **deploy de produção com aprovação humana**.

## Como o produto funciona (o modelo mental)

Antes dos comandos, entenda os três papéis. Toda a **separação de funções (SoD)** é imposta pelo
**GitHub** — o app não decide quem promove nem quem faz o deploy.

| Papel | O que faz | Onde | Quem controla |
|---|---|---|---|
| **Autor** | Escolhe um Genie Space no DEV e clica **"Preparar promoção"**. O app abre um **PR em rascunho** no repositório de conteúdo; os checks rodam no rascunho (é o "test drive"). | No app | Qualquer usuário do app |
| **Responsável Técnico** | Revisa o rascunho, marca como **pronto (ready)** e faz o **merge** — este é o ato de *promover*. | No GitHub (manual) | Permissão `Write` no repositório |
| **Plataforma** | **Aprova o deploy** no gate do Environment `prod`. | No GitHub (manual) | Ser *required-reviewer* do Environment |

O app guarda **um único papel próprio**: `admin` (**Administrador da Plataforma**), que só serve
para liberar o console de administração do próprio app. Nada mais de autorização vive no app.

> 🔒 **Detalhe de segurança que vale saber desde já.** O service principal do app **nunca** marca
> um PR como pronto — ele só **abre** ou **rebaixa** um PR para rascunho. Marcar como pronto (=
> promover) é sempre uma ação humana, feita no GitHub. Rebaixar para rascunho é seguro (é o oposto
> de promover), então o app pode fazer isso sozinho quando o conteúdo muda.

## Os dois repositórios

O produto vive em dois repositórios separados — isto é proposital:

```text
GitHub
├── repositório do engine    (código do app + lógica do pipeline)  ← este acelerador, portável
└── repositório de conteúdo  (definições dos Spaces + workflows + engine.lock)  ← recebe as promoções

Databricks
├── workspace DEV            (autoria nativa no Genie + avaliação ao vivo)
└── workspace PROD           (app + Lakebase + Spaces governados)
```

Os **workflows de CI/CD** (`pr-checks.yml`, `deploy.yml`) vivem no repositório **de conteúdo**,
porque o GitHub Actions só dispara workflows no repositório onde o PR acontece. O app roda em PROD
porque seu estado de workflow e auditoria precisa sobreviver aos resets periódicos do DEV.

## O caminho, em 16 etapas

Você vai seguir estas etapas na ordem. As primeiras 6 são de **entendimento e preparação** (nenhuma
credencial ainda); da 7 em diante você começa a provisionar de verdade.

1. Entender a topologia suportada
2. Combinar a fronteira de segurança e suporte
3. Escolher instalação limpa ou com amostra
4. Coletar os valores da instalação
5. Preparar os pré-requisitos
6. Criar e configurar os dois repositórios
7. Preparar o Databricks e o CI
8. Fazer o bootstrap do plano de controle (uma vez só)
9. Conceder ao SP do app seu acesso de runtime
10. Provisionar o acesso ao DEV entre workspaces
11. Configurar o GitHub App
12. Configurar os controles de governança
13. Rodar a primeira promoção de ponta a ponta
14. Recursos opcionais
15. Operação do dia a dia
16. Solução de problemas

> ✅ **Dica.** Se você só quer entender o produto, leia as etapas 1–2. Se vai instalar, siga tudo
> na ordem. As etapas marcadas com **(uma vez só)** não se repetem; as marcadas com **(recorrente)**
> voltam na operação do dia a dia.

---

## 1. Entender a topologia suportada · (uma vez só)

Você já viu o desenho na seção "Os dois repositórios" acima. O ponto a fixar: **o app roda em
PROD**, mas alcança o DEV através de um SP de transporte dedicado — e, antes de cada operação no
DEV, ele confere ao vivo o ACL do Space para o humano que está logado. O DEV é onde a autoria
acontece (Genie nativo); o PROD é onde ficam o app, o Lakebase e os Spaces governados.

### Compatibilidade com o Lakebase

O bundle declara `database_instances` e o app usa um recurso `database`. Hoje o Databricks cria um
projeto Autoscaling por trás dessa API de compatibilidade, e a automação de DABs existente continua
funcionando. **Mantenha este caminho criado pelo bundle** para o runtime atual.

Não crie um projeto Postgres avulso e o conecte com um recurso `postgres` do app: os stores ainda
chamam a Database API para gerar credenciais. O `postgres_projects` é o modelo nativo do futuro, mas
adotá-lo exige uma migração coordenada de bundle e runtime. Veja a
[orientação de upgrade do Databricks](https://docs.databricks.com/aws/en/oltp/upgrade-to-autoscaling).

> ✅ **Ao final desta etapa:** você entende o desenho DEV/PROD + engine/conteúdo e sabe que o
> Lakebase fica pelo caminho criado pelo bundle.

## 2. Combinar a fronteira de segurança e suporte · (uma vez só)

Antes de instalar, concorde com estas condições. Elas existem para você não ser surpreendido depois.

1. **Fronteira de contribuição confiável.** Os workflows de PR atuais rodam código do repositório em
   runners self-hosted com credenciais Databricks. Use repositórios privados/confiáveis ou runners
   efêmeros isolados. **Não** rode código de fork público arbitrário em um runner privilegiado
   persistente. A amostra ainda reutiliza a credencial de deploy do PROD para validar PRs, e essa
   identidade é admin de workspace. Para produção, use uma identidade de validação de menor
   privilégio separada para os PRs e reserve a credencial admin de deploy para o Environment `prod`
   protegido. Se mantiver a identidade compartilhada, todo contribuidor cujo PR pode rodar está
   dentro da fronteira de confiança do administrador de PROD.
2. **Nomes de arquivo de conteúdo são entrada confiável.** Os loops de shell dos workflows consomem
   slugs derivados de nomes de arquivo. Restrinja escritas de conteúdo a atores confiáveis até que a
   validação de slug e o repasse ao shell sejam endurecidos.
3. **Runners dedicados.** Não reutilize um runner de estação de trabalho genérica. Dê aos jobs do
   engine e de conteúdo rótulos/diretórios distintos, ou use um grupo de runners da organização
   restrito a esses repositórios.
4. **Papéis humanos separados.** Use pessoas diferentes para quem solicita a promoção (Autor) e
   quem aprova o deploy (Plataforma). O `Prevent self-review` do GitHub só bloqueia quem inicia o
   workflow; ele não mapeia o Autor registrado pelo app para um login do GitHub.
5. **Sem concessão automática de dados.** O deploy reconcilia apenas o `CAN_RUN` do Genie. O acesso
   a tabelas no Unity Catalog permanece externo.
6. **Nenhuma licença de software está incluída.** Adicione uma antes de distribuir o acelerador fora
   da organização pretendida.

Para produção, também fixe as GitHub Actions de terceiros em SHAs de commit revisados — os workflows
de amostra ainda usam tags mutáveis.

> ⚠️ **Sobre os helpers de provisionamento.** Eles são apoios de bootstrap, não ferramentas
> endurecidas de gestão de segredos. As implementações atuais passam algumas credenciais de uso
> único por argumentos de linha de comando (`gh ... --body`, `databricks ... --string-value`), o que
> pode expor valores à inspeção de processos em um host multiusuário. Antes de usar em produção,
> altere essas chamadas para consumir de stdin, ou rode os helpers apenas de uma máquina de
> administrador isolada e rotacione as credenciais depois.

> ✅ **Ao final desta etapa:** você aceitou (ou planejou endurecer) cada item da fronteira de
> segurança.

## 3. Escolher instalação limpa ou com amostra · (uma vez só)

Decida um caminho antes de mexer nos arquivos.

### Instalação limpa — recomendada

- Remova o conteúdo de amostra, mas mantenha um `src/.gitkeep` versionado. Os dois workflows de
  conteúdo copiam `content/src/` antes da primeira promoção, e o Git não preserva diretório vazio.
- Use seus próprios catálogos `dev_<dominio>` e `prod_<dominio>` e suas tabelas existentes.
- Comece com `APP_SPACE_SLUGS={}`.
- Crie os primeiros arquivos de conteúdo através de uma promoção real, feita pelo app.

```bash
mkdir -p /caminho/para/genie-spaces-content/src
touch /caminho/para/genie-spaces-content/src/.gitkeep
git -C /caminho/para/genie-spaces-content add src/.gitkeep
```

### Instalação com amostra

- Mantenha o conteúdo de amostra propositalmente.
- Recrie cada tabela, grupo, Space e endpoint referenciados nos seus workspaces.
- Revise o notebook de setup sintético antes de rodá-lo.
- Lembre-se de que a renderização cria uma definição de job `setup`; nenhum workflow roda esse job
  automaticamente.

> ⚠️ Nunca faça deploy do conteúdo de amostra sem alterações em um workspace não relacionado.

> ✅ **Ao final desta etapa:** você escolheu limpa (recomendada) ou amostra, e sabe o que isso
> implica.

## 4. Coletar os valores da instalação · (uma vez só)

Anote estes valores em uma planilha segura do operador. Tê-los à mão agora poupa idas e vindas
depois.

| Valor | Exemplo |
|---|---|
| Repositório do engine | `<owner>/genie-promote-cicd` |
| Repositório de conteúdo | `<owner>/genie-spaces-content` |
| Perfis CLI DEV / PROD | `<perfil-dev>` / `<perfil-prod>` |
| URLs dos workspaces DEV / PROD | `https://...cloud.databricks.com` |
| IDs de warehouse DEV / PROD | um warehouse compatível com Genie em cada workspace |
| Domínio | `vendas`, gerando `dev_vendas` e `prod_vendas` |
| Nome do Lakebase | `genie-promote-vendas` |
| Endpoint do revisor | um endpoint de chat disponível em PROD |
| Grupo de Autores | o grupo que recebe `CAN_USE` no app |
| Responsável Técnico | login do GitHub (com permissão `Write`) |
| Plataforma (aprovadores do deploy) | e-mails Databricks + logins GitHub (required-reviewers) |
| Administradores da Plataforma | e-mails Databricks (bit `admin` do app) |
| IDs iniciais de Spaces DEV | Spaces existentes que o SP de DEV deve gerenciar |

Use um SQL warehouse Pro ou Serverless suportado pelo Genie. Anote os schemas e tabelas que cada
Space vai referenciar e confirme que o público pretendido já tem os privilégios de dados.

> ✅ **Ao final desta etapa:** você tem uma planilha com todos os valores que o resto do guia pede.

## 5. Preparar os pré-requisitos · (uma vez só)

### Recursos do Databricks

Os **dois** workspaces precisam ter:

- Unity Catalog com os bindings de metastore/catálogo necessários;
- Genie;
- um SQL warehouse Pro/Serverless rodando ou iniciável;
- os usuários e grupos necessários.

Para autorização de Spaces DEV baseada em grupo, use grupos de nível de conta atribuídos aos dois
workspaces com a mesma composição. O guard de autorização compara os nomes de grupo do chamador
PROD verificado com o ACL do Space DEV; dois grupos locais de workspace que só compartilham um nome
de exibição **não** são o mesmo principal de segurança. Se grupos de conta compartilhados não
estiverem disponíveis, conceda e avalie o acesso ao DEV por usuário individual.

O **PROD** também precisa de:

- Databricks Apps;
- autorização de usuário dos Databricks Apps habilitada para o escopo `dashboards.genie`;
- Lakebase;
- um endpoint de Model Serving compatível com chat, para o revisor.

O operador de setup precisa de administração de workspace/conta para o bootstrap de identidade,
permissões de catálogo, secret scopes, permissões do app e o primeiro deploy do plano de controle.

### Máquina do operador

Instale:

- Databricks CLI `1.4.0` (a mesma versão dos workflows);
- GitHub CLI;
- Git, Bash, Python 3.12, Node.js/npm, `rsync`, `curl` e OpenSSL;
- acesso de rede às APIs dos dois workspaces, ao GitHub, ao PyPI e ao npm.

Confira a autenticação sem depender de um perfil implícito:

```bash
databricks --version
databricks auth profiles
databricks current-user me -p <perfil-dev>
databricks current-user me -p <perfil-prod>
gh auth status
```

Use nomes de perfil explícitos ao longo de todo o guia.

### GitHub

Você precisa de:

- permissão para criar/configurar os dois repositórios;
- os recursos de branch protection e Environment protegido;
- um login do GitHub para o Responsável Técnico e, se `Prevent self-review` se aplicar a quem
  dispara o deploy, um aprovador/mergeador distinto;
- identidades humanas distintas para o Autor e para quem aprova o deploy;
- uma estratégia de runners que satisfaça a fronteira de contribuição confiável.

Se o repositório do engine for privado, o `GITHUB_TOKEN` padrão do repositório de conteúdo não
consegue fazer o checkout dele. Adicione uma credencial fine-grained com `Contents: read` **apenas**
no repositório do engine, como o secret `ENGINE_REPO_READ_TOKEN` do repositório de conteúdo. Adicione-a
aos três checkouts do engine travado: os jobs `validate` e `eval-run` do `pr-checks.yml` e o job de
deploy do `deploy.yml`:

```yaml
- name: Checkout app repo (locked pipeline logic)
  uses: actions/checkout@v4
  with:
    repository: ${{ env.APP_REPO }}
    ref: ${{ steps.engine.outputs.sha }}
    path: app
    token: ${{ secrets.ENGINE_REPO_READ_TOKEN }}
```

Os workflows de amostra não incluem esse token. Se você não quiser uma credencial entre
repositórios, a alternativa como está é tornar o repositório do engine legível para os workflows de
conteúdo.

> ✅ **Ao final desta etapa:** os dois workspaces têm os recursos necessários, sua máquina está
> equipada e autenticada, e o GitHub está pronto para receber os dois repositórios.

## 6. Criar e configurar os dois repositórios · (uma vez só)

Crie cópias, pertencentes à sua organização, de:

- este repositório do engine;
- o repositório de conteúdo companheiro.

Repositórios privados são o padrão mais seguro para o modelo de runners atual.

### Fixe a revisão do engine

No repositório de conteúdo:

1. Defina `APP_REPO` em `.github/workflows/pr-checks.yml` e em `.github/workflows/deploy.yml`.
2. Coloque um SHA de commit completo do engine (do `main` protegido ou de um release imutável
   confiável) no `engine.lock`.
3. Faça o commit das mudanças de workflow e do lock juntas.

```bash
git -C /caminho/para/genie-promote-cicd rev-parse HEAD
git -C /caminho/para/genie-spaces-content rev-parse HEAD
```

O `engine.lock` deve conter exatamente 40 caracteres hexadecimais minúsculos. O verificador de
prontidão espera que ele seja igual ao checkout do engine em teste. Os workflows validam o formato,
não a procedência — então verifique a alcançabilidade antes de aceitar uma mudança de lock:

```bash
ENGINE_SHA="$(tr -d '[:space:]' < /caminho/para/genie-spaces-content/engine.lock)"
git -C /caminho/para/genie-promote-cicd fetch origin main
git -C /caminho/para/genie-promote-cicd merge-base --is-ancestor "$ENGINE_SHA" origin/main
```

O último comando deve terminar com código `0`. Proteja o `main` do engine para que esse check
implique que o código travado passou pela revisão e pelos checks do repositório do engine.

### Substitua toda a configuração de amostra

Alterar só o `databricks.yml` não basta. Revise cada linha:

| Repositório/arquivo | Mudanças necessárias |
|---|---|
| Engine `databricks.yml` | `domain`, host/warehouse DEV, warehouse PROD, nome/database do Lakebase e a allowlist de acesso direto documentada. A variável de allowlist não é aplicação. |
| Engine `scripts/render.sh` | O default de `DOMAIN` e quaisquer nomes de arquivo/exibição de dashboard/setup de amostra retidos. |
| Engine `scripts/build_promote_app.sh` | `APP_DOMAIN`, e-mails de Administrador, repo de conteúdo, URL do engine, endpoint do revisor, mapa de slugs e o endpoint do KA fixo. |
| Engine `scripts/provision_ci.sh` | Perfil/host PROD, nome do SP, catálogo PROD, warehouse e repositório. |
| Engine `scripts/provision_dev_sp.sh` | Prefira `DEV_PROFILE`, `DEV_CATALOG`, warehouse, nome do SP, scope e IDs de Space explícitos. |
| Workflows de conteúdo | `APP_REPO`, `APP_SPACE_SLUGS`, rótulos de runner e configurações específicas do domínio. |
| Conteúdo `engine.lock` | SHA de commit completo do engine revisado. |
| Conteúdo `src/` | Remova a amostra para uma instalação limpa, ou substitua cada artefato propositalmente. |

Adicione entradas explícitas ao bloco `app.yaml` gerado em `scripts/build_promote_app.sh`:

```yaml
  - name: APP_LLM_ENDPOINT
    value: "<endpoint-do-revisor-prod>"
  - name: APP_REPO_URL
    value: "https://github.com/<owner>/genie-promote-cicd"
```

Para o primeiro deploy:

```yaml
  - name: APP_SPACE_SLUGS
    value: '{}'
```

Defina também `APP_GH_REPO` como o repositório de conteúdo e **remova** todo e-mail, endpoint, ID de
Space e nome de repositório de amostra do manifesto do app gerado.

Use um valor JSON canônico de `APP_SPACE_SLUGS` tanto no `build_promote_app.sh` quanto no job
`eval-run` de conteúdo. Uma instalação limpa e vazia usa slugs reversíveis `s_<id-do-space>` e não
precisa de pins. Se você escolher um slug fixo amigável, os dois mapas precisam ser byte-idênticos;
caso contrário o job de eval ao vivo não consegue recuperar o ID do Space DEV e o script atual
degrada para um aviso verde.

Use buscas direcionadas como apoio de revisão, não como regra de exclusão automática:

```bash
rg -n "<termos-de-amostra>|<seu-dominio>|genie-spaces-content|s_[0-9a-f]+|ka-[a-z0-9-]+-endpoint" \
  databricks.yml scripts app engine_api .github
```

Correspondências em testes e fixtures podem permanecer. Manifestos de deploy, workflows e defaults
de provisionamento precisam ser intencionais.

### Endureça os workflows antes de configurar credenciais

Faça o commit destas mudanças enquanto os novos repositórios ainda não têm credenciais Databricks
ativas. O primeiro push pode mostrar um job de deploy falho ou não roteável, mas ele não consegue
alterar um workspace:

- remova as condições `if` de nível de job dos jobs `validate` (engine), dos dois jobs de PR de
  conteúdo e do `deploy-prod` de conteúdo; configuração faltante deve **falhar**, não pular;
- adicione um primeiro passo fail-closed a todo job com credencial. Verifique host, client ID,
  secret e o warehouse do job. O `deploy-prod` também deve verificar os dois valores `BUNDLE_DEV_*`
  antes de qualquer mutação;
- remova o `paths-ignore` de Markdown/docs dos workflows cujos nomes de check serão obrigatórios. O
  workflow de deploy pode manter o filtro só-docs porque não é um check obrigatório de PR;
- troque as referências de validação PROD do engine/conteúdo pelos nomes `DATABRICKS_VALIDATION_*`
  provisionados na etapa 7; mantenha `DATABRICKS_PROD_*` só no `deploy-prod`;
- faça slugs de eval não resolvidos falharem antes do `check_eval_run.py` rodar. Não aceite o aviso
  atual de slug-não-resolvido como prova de que a avaliação ao vivo obrigatória executou.

Um passo mínimo de configuração pode mapear o warehouse específico do job para `REQUIRED_WAREHOUSE_ID`:

```yaml
- name: Verify required configuration
  shell: bash
  run: |
    required=(DATABRICKS_HOST DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET REQUIRED_WAREHOUSE_ID)
    for name in "${required[@]}"; do
      [ -n "${!name:-}" ] || { echo "::error::$name is not configured"; exit 1; }
    done
```

Defina `REQUIRED_WAREHOUSE_ID` a partir da variável de validação, DEV ou PROD apropriada àquele job.
No `deploy-prod`, inclua `BUNDLE_DEV_HOST` e `BUNDLE_DEV_WAREHOUSE_ID` em `required`.

No job `eval-run` de conteúdo, adicione isto após os passos de slug-alterado e checkout-do-engine:

```yaml
- name: Require resolvable DEV Space IDs
  working-directory: app
  run: |
    for slug in ${{ steps.changed.outputs.slugs }}; do
      python3 - "$slug" <<'PY'
    import json
    import os
    import sys

    slug = sys.argv[1]
    pins = json.loads(os.environ.get("APP_SPACE_SLUGS") or "{}")
    resolved = next((space_id for space_id, pinned in pins.items() if pinned == slug), None)
    resolved = resolved or (slug[2:] if slug.startswith("s_") and len(slug) > 2 else None)
    if not resolved:
        raise SystemExit(f"EVAL-RUN cannot resolve DEV Space ID for slug {slug!r}")
    PY
    done
```

> ✅ **Ao final desta etapa:** os dois repositórios existem, o engine está fixado por SHA no
> `engine.lock`, toda a configuração de amostra foi substituída e os workflows estão endurecidos —
> tudo isso antes de qualquer credencial entrar em jogo.

## 7. Preparar o Databricks e o CI · (uma vez só)

### Crie a base de dados

Crie ou selecione:

- catálogos `dev_<dominio>` e `prod_<dominio>`;
- todos os schemas/tabelas referenciados;
- um warehouse compatível com Genie por workspace;
- os privilégios de tabela do público pretendido.

Verifique o acesso físico com as identidades que vão usar os dados. O pipeline de promoção não
semeia nem concede dados de produção, a menos que você retenha e rode deliberadamente o job de setup
de amostra.

### Provisione o SP de deploy do PROD

O `scripts/provision_ci.sh` cria a identidade de deploy, seus grants de base, uma credencial OAuth e
o required-reviewer do Environment no GitHub. Ele é enviesado para amostra e **precisa ser editado
antes de usar**:

- defina perfil/host PROD, nome do SP, catálogo e warehouse;
- crie primeiro o Environment `prod` do repositório de conteúdo;
- mude suas escritas no GitHub para gravar `DATABRICKS_PROD_*` como variáveis/secrets do
  **Environment** `prod`, não valores de nível de repositório, e envie o secret ao `gh secret set`
  via stdin;
- rode-o apenas para o repositório de conteúdo, no modelo de identidade dividida de produção;
- lembre-se de que ele cria um novo secret OAuth a cada invocação.

```bash
gh api -X PUT repos/<owner>/genie-spaces-content/environments/prod

# O bloco de escrita no GitHub do helper editado deve ter esta forma:
gh variable set DATABRICKS_PROD_HOST --repo "$GH_REPO" --env prod --body "$PROD_HOST"
gh variable set DATABRICKS_PROD_SP_CLIENT_ID --repo "$GH_REPO" --env prod --body "$SP_APP_ID"
printf '%s' "$SECRET" |
  gh secret set DATABRICKS_PROD_SP_SECRET --repo "$GH_REPO" --env prod
gh variable set DATABRICKS_PROD_WAREHOUSE_ID --repo "$GH_REPO" --env prod \
  --body "$PROD_WAREHOUSE_ID"
```

Depois de editar o helper:

```bash
PROD_WAREHOUSE_ID=<id-warehouse-prod> \
STEWARD_GH=<login-github-do-aprovador> \
GH_REPO=<owner>/genie-spaces-content \
  bash scripts/provision_ci.sh
```

> ℹ️ A variável `STEWARD_GH` do script é o login do GitHub de quem vai aprovar o deploy (a
> **Plataforma**, o required-reviewer do Environment). O nome da variável no script é legado; o
> papel hoje é "Plataforma".

Registre o application ID e o ID numérico (SCIM) do SP de deploy que o script imprime. Não publique
a credencial admin dele no repositório do engine nem no scope do repositório de conteúdo.

> ⚠️ **Gotcha que economiza horas.** O deployer atual precisa ser **admin de workspace** para o
> binding App/Lakebase. Adicione o CI SP explicitamente ao grupo `admins` do PROD:

```bash
ADMIN_GROUP_ID="$(databricks groups list -p <perfil-prod> \
  --filter 'displayName eq "admins"' -o json |
  python3 -c 'import json,sys; d=json.load(sys.stdin); xs=d if isinstance(d,list) else d.get("Resources",d.get("resources",[])); print(xs[0]["id"])')"

databricks groups patch "$ADMIN_GROUP_ID" -p <perfil-prod> --json \
  '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"members","value":[{"value":"<id-numerico-do-ci-sp>"}]}]}'
```

Revalide os privilégios de Unity Catalog do SP de deploy contra suas funções reais. Ele não concede
mais acesso a dados a públicos declarados. Mantenha `CREATE_SCHEMA` só se essa identidade for rodar o
seed de amostra, e remova o `MANAGE` legado quando nenhuma ação de deploy exigir.

### Provisione um SP de validação de PR separado

Crie um service principal PROD **não-admin** para validar PRs do engine/conteúdo. Ele precisa de
acesso ao workspace, `CAN_USE` no warehouse de validação e leitura suficiente para resolver as
tabelas, grants efetivos, usuários e grupos PROD usados pelo `check_audience.py`; ele **não** deve
entrar em `admins` nem mutar os recursos do app/Lakebase.

```bash
VALIDATION_SP_JSON="$(databricks service-principals create \
  --display-name genie-promote-validation-sp -p <perfil-prod> -o json)"
VALIDATION_SP_APP_ID="$(printf '%s' "$VALIDATION_SP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["applicationId"])')"
VALIDATION_SP_NUM_ID="$(printf '%s' "$VALIDATION_SP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
unset VALIDATION_SP_JSON

databricks service-principals patch "$VALIDATION_SP_NUM_ID" -p <perfil-prod> --json \
  '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"entitlements","value":[{"value":"workspace-access"}]}]}'

databricks warehouses update-permissions <id-warehouse-prod> -p <perfil-prod> --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$VALIDATION_SP_APP_ID\",\"permission_level\":\"CAN_USE\"}]}"
databricks grants update catalog prod_<dominio> -p <perfil-prod> --json \
  "{\"changes\":[{\"principal\":\"$VALIDATION_SP_APP_ID\",\"add\":[\"USE_CATALOG\"]}]}"
```

Conceda `USE_SCHEMA`/`SELECT` apenas onde o preflight de público precisa inspecionar acesso efetivo.
Autentique uma vez como esse SP e prove que `users list`, `groups list`, `tables get` e `grants get`
funcionam para objetos representativos. Se o workspace não expõe essas leituras sem admin, o check de
público atual não consegue oferecer uma divisão de menor privilégio ali; mantenha a fronteira de
contribuição confiável e trate isso como uma lacuna de produção, em vez de silenciosamente promover
a identidade de validação a admin.

Crie uma credencial de validação e grave-a nos dois repositórios:

```bash
VALIDATION_CRED_JSON="$(databricks service-principal-secrets-proxy create \
  "$VALIDATION_SP_NUM_ID" -p <perfil-prod> -o json)"
VALIDATION_SP_SECRET="$(printf '%s' "$VALIDATION_CRED_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
unset VALIDATION_CRED_JSON

for REPO in <owner>/genie-promote-cicd <owner>/genie-spaces-content; do
  gh variable set DATABRICKS_VALIDATION_HOST --repo "$REPO" --body '<url-workspace-prod>'
  gh variable set DATABRICKS_VALIDATION_SP_CLIENT_ID --repo "$REPO" \
    --body "$VALIDATION_SP_APP_ID"
  gh variable set DATABRICKS_VALIDATION_WAREHOUSE_ID --repo "$REPO" \
    --body '<id-warehouse-prod>'
  printf '%s' "$VALIDATION_SP_SECRET" |
    gh secret set DATABRICKS_VALIDATION_SP_SECRET --repo "$REPO"
done
unset VALIDATION_SP_SECRET VALIDATION_SP_APP_ID VALIDATION_SP_NUM_ID REPO
```

No `ci.yml` do engine e no job `validate` do `pr-checks.yml` de conteúdo, troque as referências de
credencial PROD por `DATABRICKS_VALIDATION_*`. **Mantenha o nome público do job `bundle validate
(prod)`** e continue validando o target `prod` do bundle. O job `eval-run` de conteúdo mantém sua
identidade DEV; o `deploy.yml` mantém `DATABRICKS_PROD_*`, que agora só resolvem após o gate do
Environment `prod`.

### Configure as credenciais dos repositórios

Configuração esperada no GitHub:

| Escopo | Variáveis | Secrets |
|---|---|---|
| Repositório do engine | `DATABRICKS_VALIDATION_HOST`, `DATABRICKS_VALIDATION_SP_CLIENT_ID`, `DATABRICKS_VALIDATION_WAREHOUSE_ID` | `DATABRICKS_VALIDATION_SP_SECRET` |
| Repositório de conteúdo | as três variáveis de validação, `DATABRICKS_DEV_HOST`, `DATABRICKS_DEV_WAREHOUSE_ID`, `DATABRICKS_DEV_SP_CLIENT_ID` | `DATABRICKS_VALIDATION_SP_SECRET`, `DATABRICKS_DEV_SP_SECRET` e `ENGINE_REPO_READ_TOKEN` quando o engine é privado |
| Environment `prod` de conteúdo | `DATABRICKS_PROD_HOST`, `DATABRICKS_PROD_SP_CLIENT_ID`, `DATABRICKS_PROD_WAREHOUSE_ID` | `DATABRICKS_PROD_SP_SECRET` |

Só o repositório de conteúdo tem o Environment `prod` protegido. Confirme que nenhum
`DATABRICKS_PROD_SP_SECRET` permanece no escopo de repositório.

Defina os valores DEV não-secretos explicitamente:

```bash
gh variable set DATABRICKS_DEV_HOST --repo <owner>/genie-spaces-content \
  --body '<url-workspace-dev>'
gh variable set DATABRICKS_DEV_WAREHOUSE_ID --repo <owner>/genie-spaces-content \
  --body '<id-warehouse-dev>'
```

`DATABRICKS_DEV_SP_CLIENT_ID` e `DATABRICKS_DEV_SP_SECRET` são preenchidos depois que a identidade de
transporte DEV for provisionada na etapa 10.

> Não habilite branch protection ainda; primeiro faça os checks reportarem com sucesso uma vez.

### Registre os runners

Dependências do runner: Git, Bash, Python 3.12, Node/npm, `rsync` e acesso de saída a
GitHub/Databricks/PyPI/npm.

Para runners com escopo de repositório, use diretórios e rótulos de instalação diferentes, por
exemplo:

```text
~/actions-runner-genie-engine   labels: self-hosted,genie-promote-engine
~/actions-runner-genie-content  labels: self-hosted,genie-promote-content
```

Atualize o `runs-on` nos workflows do engine e de conteúdo para casar com esses rótulos. Um grupo de
runners da organização restrito aos dois repositórios é uma alternativa.

```yaml
# Jobs do workflow do engine
runs-on: [self-hosted, genie-promote-engine]

# Jobs do workflow de conteúdo
runs-on: [self-hosted, genie-promote-content]
```

Verifique que cada runner completa um job inofensivo só-de-checkout antes de lhe dar credenciais de
workspace. Mantenha o runner isolado de cargas não relacionadas.

> ✅ **Ao final desta etapa:** os SPs de deploy e de validação existem, as credenciais estão nos
> lugares certos (validação no repo, PROD só no Environment), e os runners estão registrados e
> testados — mas a branch protection ainda não foi ligada.

## 8. Fazer o bootstrap do plano de controle · (uma vez só)

Faça isto **antes** do primeiro deploy de conteúdo. O `scripts/deploy_attempt.py` verifica de
propósito, no preflight, que o app e seu service principal **já existem**.

Rode o bootstrap como o **mesmo CI service principal** usado pelos workflows. O bundle usa estado
com escopo de identidade; fazer o bootstrap como humano e depois deployar como o CI SP pode criar um
segundo estado-raiz e conflitos de recursos.

Crie um secret de bootstrap de vida curta para o CI SP com o perfil admin do PROD:

```bash
BOOTSTRAP_JSON="$(databricks service-principal-secrets-proxy create \
  <id-numerico-do-ci-sp> -p <perfil-prod> -o json)"
BOOTSTRAP_SECRET_ID="$(printf '%s' "$BOOTSTRAP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

cleanup_bootstrap() {
  unset DATABRICKS_CLIENT_SECRET DATABRICKS_CLIENT_ID DATABRICKS_AUTH_TYPE DATABRICKS_HOST
  unset DATABRICKS_BUNDLE_ENGINE
  if [ -n "${BOOTSTRAP_SECRET_ID:-}" ]; then
    if ! databricks service-principal-secrets-proxy delete \
      <id-numerico-do-ci-sp> "$BOOTSTRAP_SECRET_ID" -p <perfil-prod>; then
      echo "ERRO: credencial temporária do CI ainda existe: $BOOTSTRAP_SECRET_ID" >&2
      return 1
    fi
    unset BOOTSTRAP_SECRET_ID
  fi
}
trap cleanup_bootstrap EXIT

export DATABRICKS_HOST="<url-workspace-prod>"
export DATABRICKS_AUTH_TYPE="oauth-m2m"
export DATABRICKS_CLIENT_ID="<application-id-do-ci-sp>"
export DATABRICKS_BUNDLE_ENGINE="direct"
export DATABRICKS_CLIENT_SECRET="$(printf '%s' "$BOOTSTRAP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
unset BOOTSTRAP_JSON
```

A partir do repositório do engine configurado:

```bash
DOMAIN=<dominio> bash scripts/render.sh prod

BUNDLE_DEV_HOST=<url-workspace-dev> \
BUNDLE_DEV_WAREHOUSE_ID=<id-warehouse-dev> \
DATABRICKS_BUNDLE_WAREHOUSE_ID=<id-warehouse-prod> \
  bash scripts/build_promote_app.sh

databricks bundle validate --strict -t prod \
  --var "domain=<dominio>" \
  --var "warehouse_id=<id-warehouse-prod>" \
  --var "dev_host=<url-workspace-dev>" \
  --var "dev_warehouse_id=<id-warehouse-dev>" \
  --var "lakebase_instance=<nome-do-lakebase>"

databricks apps deploy -t prod --auto-approve \
  --var "domain=<dominio>" \
  --var "warehouse_id=<id-warehouse-prod>" \
  --var "dev_host=<url-workspace-dev>" \
  --var "dev_warehouse_id=<id-warehouse-dev>" \
  --var "lakebase_instance=<nome-do-lakebase>"
```

O `databricks apps deploy` é o wrapper de projeto Databricks Apps da CLI: ele usa a raiz e o estado
com escopo de identidade deste bundle e então inicia o app. Deployes de conteúdo em regime
permanente chamam `bundle deploy` através do `deploy_attempt.py`, contra a mesma raiz/estado. Se o
deploy de projeto não existir em uma CLI futura, o bootstrap equivalente é `bundle deploy` seguido de
`bundle run promote_app` sob a mesma identidade CI.

> ⚠️ **Este deploy engine-only autônomo é seguro APENAS antes do primeiro deploy de conteúdo.**
> Depois que o bundle gerencia qualquer Space, dashboard ou recurso de setup em PROD, **nunca** rode
> um deploy completo de um checkout só-do-engine: seu `src/` vazio pode ser interpretado como um
> conjunto desejado vazio e **remover conteúdo gerenciado**. Todo deploy completo posterior precisa
> sobrepor o repositório de conteúdo completo, exatamente como o `deploy.yml` faz.

Remova o secret temporário mesmo que você mantenha a credencial de vida mais longa do GitHub Actions:

```bash
cleanup_bootstrap
trap - EXIT
```

Verifique:

```bash
databricks apps get genie-promote-app -p <perfil-prod> -o json
databricks database get-database-instance <nome-do-lakebase> -p <perfil-prod> -o json
```

Resultados esperados:

- estado do app `RUNNING` e uma URL não vazia;
- estado do Lakebase `AVAILABLE`;
- logs de inicialização mostram migrações bem-sucedidas no schema dedicado `genie_promote`.

> OAuth é necessário para `databricks apps logs`. Um perfil PAT consegue checar o status do app mas
> pode não conseguir transmitir os logs.

> ✅ **Ao final desta etapa:** o app está `RUNNING` com URL, o Lakebase está `AVAILABLE` e as
> migrações rodaram. O plano de controle existe.

## 9. Conceder ao SP do app seu acesso de runtime · (uma vez só)

Resolva o SP do app:

```bash
APP_SP="$(databricks apps get genie-promote-app -p <perfil-prod> -o json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')"
```

Conceda:

- `CAN_USE` no warehouse PROD;
- `USE_CATALOG` em `prod_<dominio>`;
- `USE_SCHEMA` em cada schema que o app precisa navegar;
- `CAN_QUERY` no endpoint do revisor;
- `CAN_QUERY` no endpoint do KA fixo (o handbook de CI/CD);
- `READ` no secret scope PROD depois que ele for criado.

Exemplos:

```bash
databricks warehouses update-permissions <id-warehouse-prod> -p <perfil-prod> --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$APP_SP\",\"permission_level\":\"CAN_USE\"}]}"

databricks grants update catalog prod_<dominio> -p <perfil-prod> --json \
  "{\"changes\":[{\"principal\":\"$APP_SP\",\"add\":[\"USE_CATALOG\"]}]}"

databricks grants update schema prod_<dominio>.<schema> -p <perfil-prod> --json \
  "{\"changes\":[{\"principal\":\"$APP_SP\",\"add\":[\"USE_SCHEMA\"]}]}"

databricks serving-endpoints update-permissions <endpoint-do-revisor> -p <perfil-prod> --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$APP_SP\",\"permission_level\":\"CAN_QUERY\"}]}"
```

Conceda a cada persona humana pretendida acesso ao app. Os papéis do app continuam sendo a
autoridade para o bit `admin`, mas Autores, Responsáveis Técnicos e Administradores da Plataforma
**todos precisam** de `CAN_USE` de nível de workspace antes de conseguir abri-lo:

```bash
databricks apps update-permissions genie-promote-app -p <perfil-prod> --json \
  '{"access_control_list":[
    {"group_name":"<grupo-de-autores>","permission_level":"CAN_USE"},
    {"group_name":"<grupo-de-responsaveis-tecnicos>","permission_level":"CAN_USE"},
    {"group_name":"<grupo-de-admins-plataforma>","permission_level":"CAN_USE"}
  ]}'
```

O deploy governado, mais adiante, garante `CAN_MANAGE` do SP do app em cada Space PROD deployado.

### Restrinja o acesso direto ao Lakebase

A variável `lakebase_direct_access_admins` registra a allowlist pretendida, mas **não é consumida**
pelo bundle nem por nenhum script. Alterá-la **não** muda as permissões do Lakebase.

Depois do bootstrap, use a UI do Lakebase e SQL Postgres para auditar as duas camadas de permissão:

1. Em **Settings → Project permissions** do projeto Lakebase, mantenha só o acesso de plataforma que
   o app e os operadores nomeados exigem. Admins de workspace mantêm o `CAN_MANAGE` padrão do projeto.
2. Resolva/crie as roles Postgres OAuth para o SP do app e para cada admin de break-glass nomeado.
3. Com `GRANT`/`REVOKE`, restrinja o acesso a database, schema, tabela e sequence a essas roles. A
   role do app precisa de acesso de criar/ler/escrever no seu schema `genie_promote`; roles humanas
   devem receber só o nível de break-glass aprovado.
4. Verifique que uma identidade listada consegue conectar e que uma não-admin não listada não
   consegue ler o schema. Registre essa evidência com a instalação.

ACLs de projeto e grants de banco Postgres são separados e não sincronizam automaticamente. Siga os
guias atuais do Databricks para
[permissões de projeto](https://docs.databricks.com/aws/en/oltp/projects/manage-project-permissions)
e [permissões de banco](https://docs.databricks.com/aws/en/oltp/projects/manage-roles-permissions).

> ✅ **Ao final desta etapa:** o SP do app tem exatamente o acesso de runtime que precisa, as
> personas humanas conseguem abrir o app e o acesso direto ao Lakebase está auditado e restrito.

## 10. Provisionar o acesso ao DEV entre workspaces · (uma vez só; parte é recorrente)

Re-resolva o SP do app no shell atual; os comandos de cópia de credencial abaixo concedem acesso de
scope a ele e não podem depender de uma variável deixada da etapa 9:

```bash
APP_SP="$(databricks apps get genie-promote-app -p <perfil-prod> -o json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')"
[ -n "$APP_SP" ] || { echo "ERRO: service principal do app não encontrado"; exit 1; }
```

Crie o secret scope DEV antes de rodar o helper:

```bash
databricks secrets create-scope genie_promote -p <perfil-dev>
```

Então:

```bash
DEV_PROFILE=<perfil-dev> \
DEV_CATALOG=dev_<dominio> \
DEV_WAREHOUSE_ID=<id-warehouse-dev> \
DEV_SPACE_IDS=<ids-de-space-existentes-separados-por-virgula> \
  bash scripts/provision_dev_sp.sh
```

O helper cria/reutiliza o SP de transporte DEV, concede acesso a workspace/warehouse/catálogo,
aplica `CAN_MANAGE` aos Spaces existentes listados e registra a credencial OAuth no scope DEV.

### Replique a credencial

O mesmo client ID/secret precisa existir em:

1. `genie_promote` do DEV, para o bookkeeping do helper;
2. `genie_promote` do PROD, para o app em execução;
3. as variáveis/secrets do repositório de conteúdo, para a avaliação DEV ao vivo.

O helper atual não cria a cópia do PROD. Uma CLI admin talvez consiga ler os secrets do DEV:

```bash
CLIENT_ID="$(databricks secrets get-secret genie_promote dev_sp_client_id \
  -p <perfil-dev> -o json |
  python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["value"]).decode())')"
CLIENT_SECRET="$(databricks secrets get-secret genie_promote dev_sp_client_secret \
  -p <perfil-dev> -o json |
  python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["value"]).decode())')"

databricks secrets create-scope genie_promote -p <perfil-prod> 2>/dev/null || true
printf '%s' "$CLIENT_ID" |
  databricks secrets put-secret genie_promote dev_sp_client_id -p <perfil-prod>
printf '%s' "$CLIENT_SECRET" |
  databricks secrets put-secret genie_promote dev_sp_client_secret -p <perfil-prod>
databricks secrets put-acl genie_promote "$APP_SP" READ -p <perfil-prod>

gh variable set DATABRICKS_DEV_SP_CLIENT_ID --repo <owner>/genie-spaces-content \
  --body "$CLIENT_ID"
printf '%s' "$CLIENT_SECRET" |
  gh secret set DATABRICKS_DEV_SP_SECRET --repo <owner>/genie-spaces-content

unset CLIENT_ID CLIENT_SECRET
```

O `get-secret` é uma API não documentada, orientada a DBUtils, e pode retornar `BAD_REQUEST` fora de
um notebook. Se isso acontecer, **não** copie secrets por logs ou arquivos. Crie uma credencial de
substituição e grave-a em todos os destinos enquanto o valor de uso único ainda está em memória:

```bash
DEV_SP_NAME=<nome-escolhido-do-sp-dev>
read -r DEV_CLIENT_ID DEV_SP_NUM_ID < <(
  databricks service-principals list -p <perfil-dev> -o json |
  python3 -c 'import json,sys; d=json.load(sys.stdin); xs=d if isinstance(d,list) else d.get("Resources",d.get("resources",[])); s=next(x for x in xs if x.get("displayName")==sys.argv[1]); print(s["applicationId"],s["id"])' "$DEV_SP_NAME"
)

DEV_CRED_JSON="$(databricks service-principal-secrets-proxy create \
  "$DEV_SP_NUM_ID" -p <perfil-dev> -o json)"
DEV_SECRET_ID="$(printf '%s' "$DEV_CRED_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
DEV_CLIENT_SECRET="$(printf '%s' "$DEV_CRED_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
unset DEV_CRED_JSON

# Bookkeeping DEV usado pelo provision_dev_sp.sh
printf '%s' "$DEV_CLIENT_ID" |
  databricks secrets put-secret genie_promote dev_sp_client_id -p <perfil-dev>
printf '%s' "$DEV_CLIENT_SECRET" |
  databricks secrets put-secret genie_promote dev_sp_client_secret -p <perfil-dev>
printf '%s' "$DEV_SECRET_ID" |
  databricks secrets put-secret genie_promote dev_sp_secret_id -p <perfil-dev>

# Runtime PROD
databricks secrets create-scope genie_promote -p <perfil-prod> 2>/dev/null || true
printf '%s' "$DEV_CLIENT_ID" |
  databricks secrets put-secret genie_promote dev_sp_client_id -p <perfil-prod>
printf '%s' "$DEV_CLIENT_SECRET" |
  databricks secrets put-secret genie_promote dev_sp_client_secret -p <perfil-prod>
databricks secrets put-acl genie_promote "$APP_SP" READ -p <perfil-prod>

# Workflow de conteúdo
gh variable set DATABRICKS_DEV_SP_CLIENT_ID --repo <owner>/genie-spaces-content \
  --body "$DEV_CLIENT_ID"
printf '%s' "$DEV_CLIENT_SECRET" |
  gh secret set DATABRICKS_DEV_SP_SECRET --repo <owner>/genie-spaces-content

unset DEV_CLIENT_ID DEV_CLIENT_SECRET DEV_SECRET_ID DEV_SP_NUM_ID DEV_SP_NAME
```

Automatize esse fluxo de escrita única antes de um rollout amplo.

> 🔁 **(recorrente)** Re-rode o `provision_dev_sp.sh` após um reset do workspace DEV e sempre que um
> Space DEV existente for adicionado ao conjunto gerenciado.

> ✅ **Ao final desta etapa:** o SP de transporte DEV existe, sua credencial está nos três lugares
> (bookkeeping DEV, runtime PROD, workflow de conteúdo) e o app consegue alcançar o DEV.

## 11. Configurar o GitHub App · (uma vez só)

Crie um GitHub App instalado **apenas** no repositório de conteúdo.

Permissões de repositório:

| Permissão | Acesso |
|---|---|
| Contents | Read and write |
| Pull requests | Read and write |
| Issues | Read and write |
| Checks | Read |
| Actions | Read |
| Administration | Read |
| Metadata | Read (automático) |

> 💡 O `Pull requests: Read and write` é o que permite ao app abrir o PR em rascunho, aplicar a
> etiqueta `revisao-pendente` e rebaixar um PR de volta a rascunho (via GraphQL). Sem ele, o fluxo
> de autoria draft-first não funciona.

Desabilite webhooks a menos que você os implemente explicitamente. Gere uma chave privada, restrinja
suas permissões de arquivo local e instale o App no repositório de conteúdo.

Depois que o plano de controle existir:

```bash
chmod 600 /caminho/para/github-app.pem
scripts/provision_github_app_secrets.sh \
  <id-do-github-app> \
  /caminho/para/github-app.pem \
  <perfil-prod> \
  <owner>/genie-spaces-content
```

O helper descobre o installation ID, grava as três chaves de scope PROD e tenta conceder `READ` ao
SP do app. Trate qualquer `WARN` como falha: o helper atualmente suprime alguns erros de verificação
de ACL/token. Ele passa o PEM por argumento de linha de comando; em host compartilhado, atualize
suas chamadas `put-secret` para ler de stdin antes de usar.

Verifique que o GitHub App consegue:

- ler o repositório;
- criar um branch e um PR;
- postar um comentário de issue/PR;
- ler check runs e anotações;
- ler workflow runs e aprovações;
- ler o Environment `prod` e a branch protection do `main`.

Apague ou arquive o PEM com segurança depois de armazená-lo e verificá-lo. Rotacione-o imediatamente
após qualquer suspeita de exposição.

> ✅ **Ao final desta etapa:** o GitHub App está instalado no repositório de conteúdo, com as chaves
> no scope PROD, e você confirmou que ele consegue abrir PRs e ler status.

## 12. Configurar os controles de governança · (uma vez só)

### Environment de produção protegido

No repositório de conteúdo:

- confirme que o Environment `prod` existe e contém as variáveis/secret de deploy da etapa 7;
- adicione a **Plataforma** (quem aprova o deploy) como *required reviewer*;
- habilite `Prevent self-review`;
- impeça bypass não confiável.

Lembre-se: quem aprova o deploy é o required-reviewer do Environment — é assim que a separação de
funções é imposta. O app apenas **reflete** esse estado, não o controla.

> 💡 A tela **Administração → Configurações → Papéis configurados** do app é um **espelho
> somente-leitura** do GitHub: ela mostra quem tem `Write` (quem faz merge) e quem é required-reviewer
> do Environment (quem aprova o deploy). A única coisa editável ali é quem é **Administrador da
> Plataforma** (o bit `admin`, que libera o console). Não há papel "Steward" para configurar.

### Branch protection do conteúdo

O GitHub não consegue exigir um contexto de check enquanto ele não reportar pelo menos uma vez. Faça
o commit do endurecimento de workflows da etapa 6 antes de adicionar credenciais, depois siga a etapa
13 até os primeiros checks de PR passarem — sem fazer merge. Volte aqui e exija:

- `bundle validate (prod)`;
- `eval-run pass-rate (dev)`;
- checks estritos e atualizados;
- enforcement para administradores;
- pelo menos uma revisão de PR aprovando;
- nenhum ator de bypass sem revisão.

Proteja o `main` do engine também. No mínimo, exija seu check `bundle validate (prod)` e revise as
mudanças do engine antes de subir o `engine.lock`. Aplique a mesma regra de "sempre reportar" ao seu
check obrigatório.

> ✅ **Ao final desta etapa:** o deploy exige aprovação humana da Plataforma e o `main` do conteúdo
> só aceita mudanças que passaram nos dois checks obrigatórios.

## 13. Rodar a primeira promoção de ponta a ponta · (uma vez só)

Prepare um Space DEV:

- ele referencia apenas tabelas `dev_<dominio>`;
- o Autor consegue acessá-lo;
- o SP de transporte DEV tem `CAN_MANAGE`;
- qualquer ACL baseado em grupo usa um grupo de nível de conta atribuído consistentemente em DEV e
  PROD, ou o Autor é concedido diretamente por usuário;
- ele tem pelo menos duas perguntas de benchmark;
- o público pretendido já tem seus privilégios de dados.

Então:

1. Abra o app PROD como **Autor**.
2. Confirme que o Space aparece.
3. Rode uma revisão e verifique que o endpoint do revisor configurado responde.
4. Clique **"Preparar promoção"**.
5. Verifique que o PR de conteúdo, **em rascunho**, contém `serialized_space`, `.title`,
   `.audience.json` e qualquer mapeamento.
6. Verifique que os dois checks obrigatórios passam contra o SHA travado do engine — os checks rodam
   no rascunho.
7. Volte à etapa 12 e habilite a branch protection com os dois contextos de check agora visíveis.
8. Como **Responsável Técnico**, obtenha a aprovação de PR exigida, marque o rascunho como **pronto
   (ready)** e faça o merge.
9. Tenha um ator diferente do GitHub, atuando como **Plataforma**, aprovar o Environment `prod`. Com
   `Prevent self-review`, quem disparou o deploy não pode também aprová-lo.
10. Verifique que o deploy completa todas as etapas: preflight, `bundle deploy`, resolução do Space,
    `CAN_MANAGE` do app, reconciliação de público e leitura ao vivo.
11. Verifique que o app mostra a linha do tempo de auditoria completa e que o Space PROD tem o
    conteúdo esperado e o público `CAN_RUN`.

> ✅ **Ao final desta etapa (e da instalação):** a instalação só está pronta quando isso tiver
> sucesso **sem um token humano do Databricks no CI**.

## 14. Recursos opcionais

### Knowledge Assistant (o handbook de CI/CD)

A revisão automática consulta **um único Knowledge Assistant (KA) fixo** — o handbook de CI/CD,
global a todos os Spaces — como fonte consultiva adicional. Os achados do KA são **consultivos e
nunca bloqueiam** a promoção. (A capacidade antiga de registrar vários KAs por Space foi removida
como simplificação; não há mais tela de gestão de KA.)

Para habilitar:

1. Crie e ative o KA (Agent Bricks) no workspace PROD.
2. Conceda `CAN_QUERY` ao SP do app no endpoint de serving do KA.
3. Aponte a variável `APP_CICD_KA_ENDPOINT` (no `app.yaml` gerado por `scripts/build_promote_app.sh`)
   para o nome do endpoint. Deixe em branco (`""`) para desabilitar o KA completamente.

> Os endpoints de KA usam o formato da **Responses API** em `/invocations`, não Chat Completions.

### Dados de amostra

O `src/setup/seed_recebiveis.py` do conteúdo de amostra vira um job `setup` do bundle **apenas**
depois que o conteúdo é sobreposto e renderizado. O deploy não o executa. Revise-o, faça o deploy
propositalmente no target correto e invoque `bundle run setup` **só** em uma instalação
descartável/demo.

### Resetar o histórico de demonstração

Pré-visualize primeiro:

```bash
python3 scripts/reset_demo_ledger.py --profile <perfil-prod>
```

Execute **apenas** na instalação de demonstração pretendida:

```bash
python3 scripts/reset_demo_ledger.py --profile <perfil-prod> \
  --execute --confirm DELETE-DEMO-LEDGER
```

Isso preserva papéis, regras, prompt do revisor e a configuração do KA.

## 15. Operação do dia a dia · (recorrente)

### Atualizar o engine

1. Faça merge e valide a mudança do engine.
2. Verifique que o commit é alcançável a partir do `main` protegido do engine ou de um release
   imutável confiável.
3. Copie o SHA de commit completo dele para o `engine.lock` do conteúdo.
4. Abra um PR de conteúdo contendo **só** a atualização revisada do lock.
5. Rode os dois checks de conteúdo contra esse SHA.
6. Faça merge e aprove o deploy pelo gate normal.

> Nunca aponte os workflows para um branch flutuante do engine em produção.

### Recuperar após um reset do DEV

1. Recrie/vincule o catálogo DEV, warehouse, usuários/grupos e Spaces. Registre qualquer nova URL de
   workspace, ID de warehouse e IDs de Space.
2. Se o host ou o warehouse mudou, atualize `DATABRICKS_DEV_HOST` e `DATABRICKS_DEV_WAREHOUSE_ID` no
   repositório de conteúdo.
3. Dispare um deploy governado **do repositório de conteúdo** para que o workflow sobreponha o `src/`
   atual completo antes de reconstruir o app. Se nenhum artefato de engine/conteúdo mudou, atualize
   um marcador versionado não-documentação, como `.deploy-trigger`, em um PR revisado; seu merge roda
   o `deploy.yml` com as novas variáveis e o Environment `prod` protegido. Isso atualiza os
   `APP_DEV_HOST`/`APP_DEV_WAREHOUSE_ID` embutidos; mudar só as variáveis do GitHub não basta.
4. Re-rode o `provision_dev_sp.sh` com os IDs de Space atuais e replique um secret recém-criado só se
   o helper reportar rotação.
5. Se os IDs de Space fixados customizados mudaram, atualize o `APP_SPACE_SLUGS` canônico tanto no
   build do app quanto no workflow de eval do conteúdo, e então redeploye o app pelo processo
   revisado de engine/lock.
6. Prove que o app reporta o novo host/warehouse do DEV e re-rode o check de eval DEV ao vivo antes
   de aceitar promoções.

### Rotacionar credenciais

- **SP de deploy PROD:** crie um novo secret, atualize o secret do Environment `prod` de conteúdo,
  prove um deploy com gate e então apague o antigo.
- **SP de validação PROD:** crie um novo secret, atualize os dois secrets de validação de nível de
  repositório, prove os dois workflows de PR e então apague o antigo.
- **SP de transporte DEV:** grave a mesma nova credencial no bookkeeping DEV, no runtime PROD e no
  GitHub de conteúdo, em uma operação controlada.
- **GitHub App:** gere/instale o novo PEM, atualize o scope PROD, verifique a emissão de token e
  então revogue a chave antiga.

### Recriação do app

> ⚠️ **Gotcha:** para **atualizar o código do app** em regime permanente, use o **caminho direto**,
> não o bundle:
>
> ```bash
> databricks workspace import-dir build/promote_app <app-src> --overwrite -p <perfil-prod>
> databricks apps deploy genie-promote-app --source-code-path <app-src> -p <perfil-prod>
> ```
>
> O caminho do bundle (`databricks apps deploy -t prod`) falha ao tentar recriar a instância
> Lakebase que já existe (`project slug already exists`). O deploy direto pula os recursos do bundle
> e atualiza só o app.

Recriar o Databricks App **rotaciona o service principal dele**. Re-resolva `APP_SP` e reaplique:

- `READ` no secret-scope PROD;
- `CAN_USE` no warehouse;
- privilégios de catálogo/schema;
- `CAN_QUERY` no revisor/KA;
- `CAN_MANAGE` nos Spaces governados existentes;
- `CAN_USE` das personas/grupos humanos no app recriado.

Não recrie nem redeploye de um checkout só-do-engine depois que o conteúdo é gerenciado. Use um
deploy governado normal de conteúdo. Se o app já estiver faltando e o preflight não conseguir
iniciar, faça a autenticação de bootstrap da etapa 8, mas primeiro faça o checkout da revisão exata
do `engine.lock`, sobreponha o `src/` completo do repositório de conteúdo, renderize e inspecione o
conjunto de recursos desejado antes de rodar `apps deploy`. Uma recuperação só-do-engine é destrutiva.

## 16. Solução de problemas

| Sintoma | Causa provável / ação |
|---|---|
| Deploy de conteúdo falha antes de mutar, com app faltando | O bootstrap único do plano de controle (CI-SP) foi pulado. |
| Workflow de conteúdo é pulado | Os `if` de nível de job ou filtros de path da amostra não foram removidos. Endureça os jobs obrigatórios como na etapa 6. |
| Checkout entre repositórios falha | `APP_REPO` errado, `engine.lock` indisponível ou falta o token de checkout do engine privado. |
| Divergência de lock/prontidão | Atualize o `engine.lock` por um PR de conteúdo revisado. |
| Deploy do app falha no binding do Lakebase | Confirme que o CI SP que faz o deploy é admin de workspace do PROD. |
| App está deployado mas parado | Use o deploy direto (`import-dir` + `apps deploy --source-code-path`) ou `bundle run promote_app`. |
| Deploy do app reporta passthrough de token de usuário desabilitado | Habilite a autorização de usuário dos Databricks Apps para `dashboards.genie`. |
| Revisão falha no Model Serving | Defina `APP_LLM_ENDPOINT`, verifique a disponibilidade do endpoint e conceda `CAN_QUERY` ao SP do app. |
| Space DEV está faltando | Re-rode o bootstrap DEV com o ID dele; verifique os ACLs de Space do Autor e do SP de transporte. |
| `DevEnvironmentNotBootstrapped` | Credenciais DEV faltando/desatualizadas no scope PROD, ou grants DEV foram perdidos. |
| Divergência com o GitHub está "desconhecida" | Adicione `Checks: read` e `Administration: read` ao GitHub App e reinstale/aprove as mudanças. |
| Check obrigatório nunca aparece | Confirme que o workflow obrigatório não tem `paths-ignore` correspondente, que todos os jobs falham em vez de pular quando falta configuração, e que o runner rotulado está online. |
| Permissão negada no schema do Lakebase | Provavelmente um humano criou o schema do app primeiro. Preserve os dados, restaure a posse; não faça `drop` levianamente. |
| Job do runner trava/falha na instalação | Verifique Node/npm, Python, egress PyPI/npm, disco e roteamento único do runner. |

Para a aceitação final do piloto, complete [docs/PILOT-GO-NO-GO.md](docs/PILOT-GO-NO-GO.md) e
armazene um manifesto de evidências redigido fora do repositório.

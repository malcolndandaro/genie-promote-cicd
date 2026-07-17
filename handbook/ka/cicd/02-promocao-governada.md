# Promoção Governada dev → prod (fluxo de CI/CD)

Guidance GERAL do fluxo de promoção governada de um Genie Space / dashboard AI/BI de dev para
produção, com revisão automática, checagens determinísticas e segregação de funções (SoD).

## O fluxo

1. **Autoria em dev** — o usuário de negócio cria/edita o Genie Space no Genie nativo do workspace
   de dev. Nada é autorado direto em produção.
2. **Solicitar promoção** — no app, o autor escolhe o espaço, confirma o Público do Space
   (`CAN_RUN`) e, opcionalmente, um de-para de tabelas. O app roda a revisão e o bot abre um
   PR `promote/<slug>` no repositório de conteúdo.
3. **Checagens determinísticas (CI)** — pré-render (reescreve `dev_ → prod_`), allowlist de
   identificadores (rejeita referências a ambiente/domínio errados), testes, `bundle validate`
   e o Preflight de Público. Rodam no CI como o service principal de produção.
4. **Revisão do agente** — o Genie Reviewer avalia o espaço contra o handbook (regras ENV/
   PII/EVAL/SQL). Achados BLOCKER travam; SUGGESTION/STYLE são advisory.
5. **Merge + gate do Steward** — após o merge, o deploy pausa em um gate de Environment do GitHub
   que exige a aprovação de um Steward distinto do solicitante (segregação de funções).
6. **Deploy** — o service principal aplica o bundle, converge apenas o Público `CAN_RUN`, preserva
   ACLs elevadas e verifica o estado final. Acesso aos dados segue o processo Terraform da CERC.

## Segregação de funções (SoD)

- O **solicitante nunca aprova a própria promoção**. O gate de Environment exige um revisor
  requerido diferente (prevent self-review). Essa é a garantia forte de SoD, na identidade do
  GitHub.
- A aprovação acontece no GitHub (deep-link), não in-app. O app reflete o status, não o falsifica.

## Boa prática: promover só o que mudou

- Promover um espaço idêntico ao que já está em produção gera um PR vazio — nenhum check roda e o
  merge é um no-op. Edite o espaço no dev (ex.: adicione um benchmark, ajuste uma instrução) antes
  de promover, para gerar um diff real.

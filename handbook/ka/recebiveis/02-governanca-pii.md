# Governança de Dados & PII — Recebíveis (CERC)

Regras de governança específicas do domínio de recebíveis que um Genie Space de produção deve
respeitar. Estas são orientações de conformidade (LGPD, sigilo bancário, regulação BCB) para o
revisor considerar ao avaliar uma promoção.

## PII e sigilo bancário

- **CPF, dados do titular, número de cartão (PAN)** são **PII de pessoa física / sigilo bancário**
  (LGPD + regulação BCB nº 4.658/4.893 sobre segurança cibernética). Exposição SEM máscara a um
  grupo não autorizado é um problema grave — deve bloquear a promoção.
- **CNPJ do cedente** é identificador de **empresa** (dado de negócio), NÃO é PII de pessoa física.
  Não é bloqueador por si só; é esperado em análises de recebíveis por cedente.
- Termos-sinal de PII em nomes de coluna a observar: `cpf`, `titular`, `portador`, `pan`, `cartao`,
  `cartão`. Uma coluna com esses termos exposta sem máscara a um grupo amplo é suspeita.

## Princípios de acesso

- Um Genie Space de produção só é útil se o **grupo consumidor de produção** tiver SELECT em TODAS
  as tabelas que o espaço referencia. Um espaço cujas fontes o grupo não pode ler é inutilizável —
  essa é a falha clássica a evitar.
- Grants de Unity Catalog aceitam apenas **principais de nível de conta** (account-level). Grupos
  locais do workspace (`users`, `admins`) NÃO podem receber grants UC — use o equivalente de conta
  (ex.: `account users`).
- Não induza cruzamento de dados entre cedentes distintos nem vazamento entre tenants nas
  instruções do espaço.

## Regulação aplicável (contexto)

- **Resolução BCB nº 264** e correlatas — registro de recebíveis de arranjos de pagamento.
- **LGPD (Lei 13.709/2018)** — tratamento de dados pessoais; minimização e finalidade.
- **Sigilo bancário (LC 105/2001)** — dados de operações financeiras.

Ao revisar uma promoção, sinalize (advisory) se um espaço parecer expor dados sensíveis sem a
devida proteção, ou se as instruções induzirem análises que cruzem fronteiras de cedente/tenant.

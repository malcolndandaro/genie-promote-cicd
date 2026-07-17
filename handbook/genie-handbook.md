# Genie/AI-BI Handbook — promotion standards

> The knowledge base the **Genie Reviewer** agent grounds on (chunked into Vector
> Search; one rule = one retrievable chunk, mirroring `bimbo_demo/bimbops_handbook`).
> The agent cites a `rule_id` from this handbook on every finding and **never
> invents rules**. Deterministic checks (JSON schema, `bundle validate`,
> identifier allowlist) already run on every PR — these rules cover the
> **semantic / policy** layer those tools cannot see. Findings are written in
> **Portuguese**.
>
> Severity gate (ADR-0002 of `bimbo_demo`, reused): any **BLOCKER** fails the
> Check Run and blocks merge; **SUGGESTION/STYLE** advise only.

## What the agent reviews
A diff of a Genie Space `serialized_space` JSON (and, for dashboards, a
`.lvdash.json`). Relevant objects: `data_sources[].identifier` (3-part
`catalog.schema.table`), `instructions`, `example_sql` / certified SQL snippets,
`benchmark_questions` (Q→SQL pairs), `warehouse_id`, `permissions`/ACLs.

---

## ENV — Catalog-per-environment

### ENV-01 — No cross-environment catalog references (BLOCKER)
*citation:* `Genie Promotion Handbook › Catalog-per-Env › ENV-01`
A Space promoted to **prod** must reference **only** `prod_<domain>.` catalogs.
Any `data_sources[].identifier` — or any catalog name hard-coded inside an
`example_sql`/certified SQL snippet — that points at `dev_`, `sbx_`, or another
environment is a **BLOCKER**. Cross-env data traffic itself risks a BCB-538
penalty. *(After pre-render, the deterministic allowlist `^prod_<domain>\.`
catches identifiers; the agent additionally catches catalog names buried inside
SQL strings that the allowlist regex over `data_sources` would miss.)*

### ENV-02 — Warehouse must be the prod warehouse (SUGGESTION)
*citation:* `Genie Promotion Handbook › Catalog-per-Env › ENV-02`
`warehouse_id` must resolve to the prod/portal workspace's warehouse, not a dev
warehouse. (Pre-render swaps it; flag if a stray dev warehouse survives.)

## PII — Bank secrecy / sensitive data

### PII-01 — No unmasked bank-secrecy / PII column exposed (BLOCKER)
*citation:* `Genie Promotion Handbook › PII › PII-01`
A prod Space must not expose, **unmasked and to an unauthorized group**, a
**personal-PII or bank-secrecy column** — **CPF** (individual), account-holder
identity/data, or raw card data (PAN). **CNPJ of a cedente is a company
identifier (business data) and is NOT a blocker by itself** — calibrated after the
S5 clean-path check false-flagged it. The control is UC column masks / row filters;
the rule ensures a promotion never widens exposure. Re-identification by joining
public data is the 538 risk. **BLOCKER only on real undue exposure.**

### PII-02 — Instructions must not invite cross-client leakage (SUGGESTION)
*citation:* `Genie Promotion Handbook › PII › PII-02`
Instructions/sample questions must not steer Genie to join across clients or
return another client's data. Relevant to the client-facing-bot fear; flag prompts
that could leak across tenant boundaries.

---

## EVAL — Trust bar for self-promotion

### EVAL-01 — Benchmark questions are required (BLOCKER)
*citation:* `Genie Promotion Handbook › Quality › EVAL-01`
A Space promoted to prod must carry **≥ N benchmark Q→SQL pairs** (start N=3).
These are what the **eval-run gate** scores on staging; with zero pairs there is
nothing to certify against and "quem valida se está certo?" has no answer.
**BLOCKER.** *(If the eval-run API is unavailable in-region, the gate degrades to
advisory — but the pairs must still exist for the agent's static review.)*

### EVAL-02 — Instructions should be specific and safe (SUGGESTION)
*citation:* `Genie Promotion Handbook › Quality › EVAL-02`
Vague instructions ("answer anything about receivables") invite hallucinated joins
and wrong numbers — Acme's "different number per team" pain. Prefer scoped,
trusted-asset-anchored guidance naming the Diamond tables to use.

---

## SQL — Conventions (STYLE)

### SQL-01 — Certified/example SQL follows Acme conventions (STYLE)
*citation:* `Genie Promotion Handbook › SQL › SQL-01`
Example/certified SQL in a Space should use UPPERCASE keywords, lowercase
identifiers, explicit `AS` aliases, and prefer **Diamond** (gold) tables over
raw/trusted. STYLE only — advisory.

---

## Rule index (for the severity gate + eval scorers)
| rule_id | severity_hint | one-liner |
|---|---|---|
| ENV-01 | BLOCKER | cross-env catalog ref in a prod Space |
| ENV-02 | SUGGESTION | non-prod warehouse survived promotion |
| PII-01 | BLOCKER | unmasked bank-secrecy/PII exposed to wrong group |
| PII-02 | SUGGESTION | instructions invite cross-client leakage |
| EVAL-01 | BLOCKER | no benchmark questions to certify against |
| EVAL-02 | SUGGESTION | vague/unsafe instructions |
| SQL-01 | STYLE | example SQL ignores Acme conventions |

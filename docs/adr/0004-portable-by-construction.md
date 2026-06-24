# Portable by construction — no hardcoded workspace bindings; everything reproducible

**Status:** accepted (2026-06-22)

Portability is a **mandatory, cross-cutting requirement**: if the workspaces
change in the future, the entire system must be easy to recreate and redeploy from
the repo + config, with **no hardcoded workspace bindings** and no manual
archaeology. This is a hard rule because the demo runs on field/demo workspaces
today and is explicitly intended to be repointed at Acme's real dev/sandbox →
prod/portal workspaces later — and, beyond that, at any future workspace pair.

## Rules (the Definition of Done for portability — applies to every slice)
1. **No hardcoded identifiers.** Workspace host/URL, `warehouse_id`, SP client-id,
   catalog/schema names, Genie space IDs, dashboard IDs, Vector Search / Model
   Serving / eval endpoint names — all come from **DABs target variables**,
   **GitHub Actions vars/secrets**, or **app config**. A grep for a literal host
   or `warehouse_id` in source = a bug.
2. **Config in one place.** Switching workspaces means editing one config surface
   (DABs target vars + tfvars/bootstrap inputs + GitHub environment vars), then
   `apply` + `bundle deploy` — not editing code.
3. **Account/UC-level objects are reproducible too.** DABs only covers
   workspace-level resources (`genie_spaces`, jobs, the app). Catalogs, schemas,
   grants, the per-domain CI/CD SP, and workspace–catalog bindings are provisioned
   by an **idempotent** path checked into the repo (mechanism below).
4. **Idempotent + re-runnable.** Re-running provisioning/deploy against a fresh
   workspace pair recreates everything; re-running against an existing one is a
   no-op. S1 is authored as a re-runnable bootstrap, not a one-time manual setup.
5. **Secrets externalized.** GitHub secrets / Databricks secrets — never embedded
   in code, config, or the `serialized_space`.
6. **`run_as` SP per target** (inherited from `bimbo_demo`).
7. **The pre-render derives from config.** The `dev_`→`prod_` prefix and the target
   `warehouse_id` (ADR-0003) come from target config, never literals — so the same
   transform works for any source/target workspace pair.

## Mechanism (chosen): all-DABs — resources + a setup job
**Catalogs and schemas are declared as DABs resources** (`catalogs`, `schemas`),
and **a DABs-deployed `setup` job** performs everything the resource layer can't
express idempotently: UC **grants** to groups, **workspace–catalog bindings**,
**synthetic seed data**, and any SP/group wiring. The whole foundation therefore
lives behind a single `databricks bundle deploy -t <target>` + a `bundle run
setup` — one toolchain, no Terraform. Targets carry the per-env vars (host via
profile, catalog prefix, warehouse, groups), so a new workspace pair is a target
config change.

Why not Terraform: it's the textbook alternative and matches Davi's reflex, but it
splits the toolchain (tf for UC, DABs for resources) and a second state store. The
all-DABs path keeps **one deploy story** and is simpler to make idempotent and to
demo. The setup job is plain SQL/SDK in-repo, so it's just as reproducible.

## Consequences
- S1 (foundation) ships as **infra-as-code + idempotent bootstrap**, not a manual
  click-through; tearing down and recreating on a new workspace pair is a
  documented, repeatable command.
- Every issue inherits the §Rules portability DoD (see issues `README.md`).
- Reinforces the BCB-538 "documented, reproducible environment isolation" posture
  as a side benefit.

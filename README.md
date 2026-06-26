# genie-promote — promote Databricks Genie Spaces & AI/BI dashboards via reviewed CI/CD

A reference pipeline that lets a **business user author a Genie Space in a dev workspace
and promote it to a prod workspace** through a governed CI/CD flow — with an **LLM reviewer
agent**, deterministic checks, an eval-run quality gate, and a **single FastAPI + Svelte front
door** that serves the API and SPA from one origin and acts on the user's behalf (OBO, in-process)
— so non-technical users never touch Git or the CLI.

Sample domain: `recebiveis` (Brazilian *receivables*) — swap it for your own.

## How it works

```
author Genie Space (dev, native UI)
        │  Request promotion (App / PR)
        ▼
GitHub Actions (self-hosted runner)
  pr-checks ── pre-render dev_→prod_  +  identifier allowlist (only prod_<domain>.* allowed)
            └─ bundle validate
  review ──── Genie Reviewer agent: handbook-grounded findings (PT) + severity gate
              · GRANT-01 (missing UC grant) and EVAL-01 (<3 benchmark Qs) are DETERMINISTIC
  /genie-fix ─ agent rewrites the serialized_space to clear fixable findings
  on merge ── eval-run gate (graceful degradation) → deploy to prod via the CI/CD SP
              (the genie_spaces + dashboard are deployed as DABs resources)
```

The promoted artifact is a Genie `serialized_space` JSON (and a dashboard `.lvdash.json`),
versioned in Git. The pipeline rebinds `dev_<domain>` → `prod_<domain>` at deploy
(DABs `${var}` doesn't reach inside the serialized space — see `docs/adr/0003`).

## Layout

| Path | What |
|---|---|
| `databricks.yml` | DABs bundle: dev/prod targets, catalogs/schemas, the prod-only `genie_spaces` + `dashboards` resources, the dev-only **Lakebase** instance + app binding (ADR-0005) |
| `genie_reviewer/` | the reviewer agent — `review_core` (parse + prompt + gate), `handbook_rules`, `grant_check`, `eval_gate`, `fix_core`, `agent` |
| `scripts/` | `pre_render.py` (rebind + allowlist), `render.sh`, `build_promote_app.sh`, `deploy_dev.sh`, `provision_ci.sh`, `verify_*.py` (incl. `verify_lakebase.py`), `check_grants.py` |
| `app/` | shared review engine (`app_logic.py`) — consumed by the FastAPI app |
| `engine_api/` | FastAPI app: JSON API (`/api/*`, OBO via forwarded token, read in-process) + serves the built Svelte SPA from the same origin |
| `web/` | Svelte 5 + Vite SPA — the front door (Meus Espaços / Revisão pipeline / Steward SoD); built to static assets the FastAPI app serves |
| `resources/`, `src/` | DABs resources, setup/seed job, the serialized_space + dashboard artifacts |
| `tests/` | unit tests (pure cores + engine API; no workspace needed) |
| `docs/adr/` | architecture decisions |

## Use it on your workspaces

1. **Set your workspaces** — edit `databricks.yml` (`workspace_host` + `warehouse_id` per target),
   or let CI inject them via repo variables (`--var`, see the workflows). Nothing else is workspace-pinned.
2. **Pre-create the domain catalogs** `dev_<domain>` / `prod_<domain>` (UI → Default Storage),
   or add a `resources/*.catalog.yml` if your deployer has `CREATE CATALOG`.
3. **Go live (CI)** — see `SETUP.md`: create the repo, run `scripts/provision_ci.sh`
   (CI/CD service principal + OAuth secret + repo vars + prod environment), register a
   self-hosted runner (workspaces with IP access lists reject hosted runners).
4. **Run the tests** — `python3 -m pytest tests/ -q`.

## Durable state (Lakebase)
Promotions, review snapshots, and an append-only audit trail are persisted in **Lakebase
(Databricks Postgres)** — a durable index + audit log + status cache over GitHub-as-truth (it never
overrides a live GitHub read; ADR-0005). Provisioned all-DABs (dev target), config-driven
(`lakebase_*` vars), app connects as its SP via short-lived OAuth — no static password. See
`SETUP.md` → *Lakebase* for the config knobs + connectivity smoke (`scripts/verify_lakebase.py`).

## Notes
- Built on the same engine pattern as a prior internal demo; the reviewer agent runs on a
  Databricks Foundation Model endpoint (Claude). Findings are in Portuguese (configurable).
- Deterministic findings (GRANT-01, EVAL-01) own their gate verdict, so a soft/omitted LLM
  answer can't flip a broken space to "pass" — the agent removes typing, not judgment.

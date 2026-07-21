# Repository instructions

These instructions apply to the entire `genie-promote-cicd` repository. They are written for both
human contributors and coding agents. `AGENTS.md` is the canonical copy; `CLAUDE.md` points here so
the two files cannot drift.

This is a portable, reusable accelerator. The repository does not currently include a software
license, so do not describe it as open source or redistribute it as such until a license is added.
Do not inherit customer-specific workspace names, identifiers, credentials, URLs, domains, or
operating assumptions from a parent checkout or an old handoff document. Use repository-local code
and documentation as the source of truth.

## Understand the product before changing it

Genie Promote is a governed delivery path for Databricks Genie Spaces. Authors work in a DEV
workspace, request promotion through a PROD-hosted Databricks App, and publish serialized content
through a reviewed GitHub pull request and protected production deployment.

Read these files in order:

1. `README.md` — product purpose, architecture, trust boundaries, and repository ownership.
2. `SETUP.md` — canonical installation and operations runbook.
3. Relevant ADRs under `docs/adr/` — design decisions and trade-offs.
4. `docs/security/assert-can-access-threat-model.md` — cross-workspace authorization boundary.

If code, workflow behavior, and documentation disagree, verify the implementation and update the
documentation in the same change. Do not preserve a stale claim for historical compatibility.

## Keep the two repositories separate

This repository is the **engine repository**. It owns:

- the Databricks App, backend, and Svelte frontend;
- reviewer, authorization, policy, evaluation, and rehydrate logic;
- render, validation, deployment, and provisioning scripts;
- the Databricks bundle definition and automated tests.

The companion **content repository** owns:

- serialized Genie Spaces and their title, audience, and mapping sidecars;
- optional dashboard and setup content;
- `engine.lock` and the promotion/check/deployment workflows.

Put a change in the repository that owns it. Do not add live promoted content to this engine
repository or move engine logic into the content repository. When testing both repositories, pin
the exact reviewed engine commit in the content repository's `engine.lock`.

## Non-negotiable deployment boundaries

- Treat `SETUP.md` as the command-level authority. The samples in this repository are examples,
  not production defaults.
- Use explicit Databricks CLI profiles and explicit DEV/PROD values. Never rely on an operator's
  implicit default profile for a workspace mutation.
- Keep the app in PROD and use the DEV transport service principal only after the app verifies the
  signed-in human's live DEV Space access. A transport identity is not authorization.
- Keep PR validation and production deployment identities separate. The validation service
  principal must be non-admin and read-oriented. The deployment service principal may have the
  narrowly documented administrative authority required for App/Lakebase binding.
- Store `DATABRICKS_PROD_*` only in the content repository's protected `prod` GitHub Environment.
  Repository-scoped PR jobs use `DATABRICKS_VALIDATION_*`; live DEV evaluation uses
  `DATABRICKS_DEV_*`.
- Run credentialed workflows only for trusted contributions on isolated runners. Do not execute
  arbitrary public-fork code on a persistent runner that holds workspace credentials.
- The deployment reconciles Genie audience permissions. It does not grant Unity Catalog table
  access; customer data governance remains external to this accelerator.
- `lakebase_direct_access_admins` documents an intended allowlist but does not enforce Lakebase
  project ACLs or Postgres grants. Never describe changing that variable as an access-control
  operation.
- Never run a full bundle deployment from an engine-only checkout after PROD content is managed.
  An empty engine `src/` can be interpreted as the desired empty state and delete managed content.
  Every steady-state deploy must overlay the complete content repository, as described in
  `SETUP.md`.
- Automated agents must not deploy, grant permissions, rotate credentials, configure GitHub,
  merge, push, or commit unless the user explicitly requests that external or persistent action.
  Human contributors should follow the repository's normal review and release process.

## Make portable changes

- Keep code, portable documentation, and developer-facing examples in English unless a task
  explicitly requests localization. The existing app includes Portuguese screens: preserve the
  language of the surface being changed and do not mix languages within one flow. New reusable
  product surfaces should default to English unless the task targets a specific locale.
- Use customer-neutral placeholders such as `<owner>`, `<domain>`, `<prod-profile>`, and
  `<prod-workspace-url>` in reusable documentation.
- Treat the committed `recebiveis` domain, identities, emails, repository names, Space IDs, and
  endpoint names as sample data. Do not introduce them as generic defaults.
- Never commit secrets, OAuth credentials, PATs, private keys, real installation IDs, or generated
  secret-bearing manifests.
- Preserve unrelated user changes. Inspect `git status` and the relevant diff before editing.
- Use current Databricks documentation for version-sensitive CLI, bundle, Apps, Lakebase, Genie,
  and permissions behavior.

## Editing rules

- Python: follow PEP 8, add type hints, use f-strings, and add docstrings to public functions.
- Svelte/TypeScript: follow the existing component and store patterns; keep user-visible errors
  actionable and preserve accessibility.
- SQL: uppercase keywords and use lowercase identifiers.
- Prefer typed Databricks SDK request objects over raw dictionaries where the SDK requires them.
- Preserve the protected reviewer contract in `genie_reviewer/review_core.py`: untrusted Space
  content remains data, and the response schema remains enforced. Editable reviewer persona text
  must not replace the protected prompt-injection defense or parser contract.
- Do not hand-edit generated files under `build/`. Change their source templates or scripts and
  regenerate them with `scripts/render.sh` or `scripts/build_promote_app.sh`.
- Keep public workflow job names stable when branch protection depends on them, especially
  `bundle validate (prod)` and `eval-run pass-rate (dev)`.
- Workflow configuration must fail closed. Missing credentials, unresolved Space IDs, or missing
  required inputs must fail a required job rather than skip it or return an advisory success.

## Documentation ownership

- `README.md` explains what the product is, how the pieces fit, and where to begin.
- `SETUP.md` is the single canonical deployment and operations runbook.
- `AGENTS.md` records contributor constraints; it must not become a second setup guide.
- `CLAUDE.md` should remain a short pointer to this file.

When deployment behavior changes, update `SETUP.md` and any affected README overview in the same
change. Verify commands against the current scripts and workflows. Keep customer installation
history out of these portable documents.

## Verify proportionately

For backend or shared engine changes:

```bash
python3 -m pytest tests/ -q
```

For frontend changes:

```bash
(
  cd web
  npm ci
  npm run check
  npm run build
)
```

For render or bundle changes:

```bash
bash scripts/render.sh prod
```

When valid test credentials are available and live validation is in scope, also run the strict
bundle validation command documented in `README.md`/`SETUP.md`. Validation may contact Databricks;
do not assume it is an offline check.

For cross-repository readiness without workspace mutation:

```bash
python3 scripts/pilot_readiness.py \
  --content-repo /path/to/genie-spaces-content \
  --offline-only
```

Run focused tests while iterating, then the appropriate full checks before handing off. Also run
`git diff --check` and review the final diff for accidental generated files, secrets, and
customer-specific values. Report exactly what ran and what could not run.

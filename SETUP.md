# Deployment and operations guide

This is the canonical runbook for deploying Genie Promote into a new Databricks DEV/PROD workspace
pair and a new GitHub owner.

The result is:

- one PROD-hosted Databricks App and its Lakebase state store;
- one DEV transport service principal for guarded cross-workspace Genie operations;
- one least-privilege PROD validation service principal for PR checks;
- one PROD workspace-admin deployment service principal available only behind the production gate;
- one GitHub App for content pull requests and status reads;
- one engine repository and one content repository;
- protected content checks and a human-gated production deployment.

The repository contains working sample values. It is not a one-command installer.

## 1. Understand the supported topology

```text
GitHub
├── engine repository   (app + pipeline code)
└── content repository  (Space definitions + workflows + engine.lock)

Databricks
├── DEV workspace       (native authoring + live eval)
└── PROD workspace      (app + Lakebase + governed Spaces)
```

The app runs in PROD because its workflow and audit state must survive DEV resets. A dedicated DEV
service principal transports Genie API calls, but the app verifies the signed-in human's live Space
ACL before every DEV operation.

### Lakebase compatibility

The bundle declares `database_instances` and the app uses a `database` resource. Databricks now
creates an Autoscaling project behind that compatibility API, and existing DAB automation continues
to work. Keep this bundle-created path for the current runtime.

Do not create an unrelated Postgres-only project and attach it with an app `postgres` resource:
the stores still call the Database API for credential generation. `postgres_projects` is the
preferred future-native model, but adopting it requires a coordinated bundle and runtime migration.
See the
[Databricks upgrade guidance](https://docs.databricks.com/aws/en/oltp/upgrade-to-autoscaling).

## 2. Security and support boundary

Before deployment, agree to these constraints:

1. **Trusted contribution boundary.** Current PR workflows run repository code on self-hosted
   runners with Databricks credentials. Use private/trusted repositories or isolated ephemeral
   runners. Do not run arbitrary public-fork code on a persistent privileged runner.
   The sample also reuses the PROD deploy credential for PR validation, and that identity is a
   workspace admin. For a production installation, use a separate least-privilege validation
   identity for pull requests and reserve the admin deploy credential for the protected `prod`
   Environment. If you retain the shared identity, every contributor whose PR can run is inside the
   PROD administrator trust boundary.
2. **Content filenames are trusted input.** Current workflow shell loops consume slugs derived from
   filenames. Restrict content writes to trusted actors until slug validation and shell handoff are
   hardened.
3. **Dedicated runners.** Do not reuse a general workstation runner. Give engine and content jobs
   distinct labels/directories, or use an organization runner group restricted to these repos.
4. **Separate human roles.** Use different people for the business requester and Steward.
   GitHub's `prevent_self_review` only blocks the workflow initiator; it does not map the business
   requester recorded by the app to a GitHub login.
5. **No automatic data grants.** The deployment reconciles Genie `CAN_RUN` only. Unity Catalog
   table access remains external.
6. **No software license is included.** Add one before distributing the accelerator outside the
   intended organization.

For a production installation, also pin third-party GitHub Actions to reviewed commit SHAs. The
sample workflows currently include mutable tags.

The provisioning helpers are bootstrap aids, not hardened secret-management tools. Their current
implementations pass some one-time credentials through command arguments (`gh ... --body` and
`databricks ... --string-value`), which can expose values to process inspection on a multi-user
operator host. Before production use, change those calls to consume stdin or run the helpers only
from an isolated administrator machine and rotate the resulting credentials afterward.

## 3. Choose clean or sample installation

Choose one path before changing files.

### Clean installation — recommended

- Remove the sample content, but keep a tracked `src/.gitkeep`. Both content workflows copy
  `content/src/` before the first promotion, and Git does not preserve an empty directory.
- Use your own `dev_<domain>` and `prod_<domain>` catalogs and existing tables.
- Leave `APP_SPACE_SLUGS={}` and `APP_KA_SEED=[]` initially.
- Create the first content files through a real promotion request from the app.

```bash
mkdir -p /path/to/genie-spaces-content/src
touch /path/to/genie-spaces-content/src/.gitkeep
git -C /path/to/genie-spaces-content add src/.gitkeep
```

### Sample installation

- Keep the committed `recebiveis` content intentionally.
- Recreate every referenced table, group, Space, and endpoint in your workspaces.
- Review the synthetic setup notebook before running it.
- Remember that rendering creates a `setup` job definition; no workflow runs that job automatically.

Never deploy the sample content unchanged into an unrelated workspace.

## 4. Collect installation values

Record these values in a secure operator worksheet:

| Value | Example |
|---|---|
| Engine repository | `acme/genie-promote-cicd` |
| Content repository | `acme/genie-spaces-content` |
| DEV / PROD CLI profiles | `acme-dev` / `acme-prod` |
| DEV / PROD workspace URLs | `https://...cloud.databricks.com` |
| DEV / PROD warehouse IDs | one Genie-compatible warehouse in each workspace |
| Domain | `sales`, producing `dev_sales` and `prod_sales` |
| Lakebase name | `genie-promote-sales` |
| Reviewer endpoint | a chat endpoint available in PROD |
| Author group | the group that receives app `CAN_USE` |
| Steward | Databricks email and GitHub login |
| Platform admins | Databricks emails |
| Initial DEV Space IDs | existing Spaces the DEV SP must manage |

Use a Pro or Serverless SQL warehouse supported by Genie. Record the schemas and tables each Space
will reference and confirm that the intended audience already has its required data privileges.

## 5. Prerequisites

### Databricks features

Both workspaces must have:

- Unity Catalog and the required metastore/catalog bindings;
- Genie;
- a running or startable Pro/Serverless SQL warehouse;
- the required users and groups.

For group-based DEV Space authorization, use account-level groups assigned to both workspaces with
the same membership. The authorization guard compares the verified PROD caller's group names with
the DEV Space ACL; two workspace-local groups that merely share a display name are not the same
security principal. If shared account groups are unavailable, grant and evaluate DEV access by
individual user instead.

PROD must also have:

- Databricks Apps;
- Databricks Apps user authorization enabled for the `dashboards.genie` scope;
- Lakebase;
- a chat-compatible Model Serving endpoint for the reviewer.

The setup operator needs workspace/account administration for identity bootstrap, catalog
permissions, secret scopes, app permissions, and the first control-plane deployment.

### Operator machine

Install:

- Databricks CLI `1.4.0`, matching the workflows;
- GitHub CLI;
- Git, Bash, Python 3.12, Node.js/npm, `rsync`, `curl`, and OpenSSL;
- network access to both workspace APIs, GitHub, PyPI, and npm.

Check authentication without relying on an implicit profile:

```bash
databricks --version
databricks auth profiles
databricks current-user me -p <dev-profile>
databricks current-user me -p <prod-profile>
gh auth status
```

Select and use explicit profile names throughout this guide.

### GitHub

You need:

- permission to create/configure both repositories;
- branch protection and protected Environment features;
- a GitHub login for the Steward and a distinct PR reviewer/merger if `Prevent self-review` applies
  to the actor who triggers deployment;
- distinct human identities for the business requester and Steward in the app;
- a runner strategy that satisfies the trusted-contribution boundary.

If the engine repository is private, the content repository's default `GITHUB_TOKEN` cannot check
it out. Add a fine-grained credential with `Contents: read` on only the engine repository as the
content-repository secret `ENGINE_REPO_READ_TOKEN`. Add it to all three locked-engine checkouts: the
`validate` and `eval-run` jobs in `pr-checks.yml`, and the deployment job in `deploy.yml`:

```yaml
- name: Checkout app repo (locked pipeline logic)
  uses: actions/checkout@v4
  with:
    repository: ${{ env.APP_REPO }}
    ref: ${{ steps.engine.outputs.sha }}
    path: app
    token: ${{ secrets.ENGINE_REPO_READ_TOKEN }}
```

The sample workflows do not include this token. If you do not want a cross-repository credential,
the as-is alternative is to make the engine repository readable to the content workflows.

## 6. Create and configure both repositories

Create organization-owned copies of:

- this engine repository;
- the companion content repository.

Private repositories are the safest default for the current runner model.

### Pin the engine revision

In the content repository:

1. Set `APP_REPO` in both `.github/workflows/pr-checks.yml` and
   `.github/workflows/deploy.yml`.
2. Put a full engine commit SHA from protected `main` or a trusted immutable release in
   `engine.lock`.
3. Commit the workflow and lock changes together.

```bash
git -C /path/to/genie-promote-cicd rev-parse HEAD
git -C /path/to/genie-spaces-content rev-parse HEAD
```

`engine.lock` must contain exactly 40 lowercase hexadecimal characters. The readiness verifier
expects it to equal the engine checkout being tested. The workflows validate the shape, not the
provenance, so verify reachability before accepting a lock bump:

```bash
ENGINE_SHA="$(tr -d '[:space:]' < /path/to/genie-spaces-content/engine.lock)"
git -C /path/to/genie-promote-cicd fetch origin main
git -C /path/to/genie-promote-cicd merge-base --is-ancestor "$ENGINE_SHA" origin/main
```

The final command must exit `0`. Protect engine `main` so this check implies that the locked code
passed the engine repository's review and checks.

### Replace all sample configuration

Changing only `databricks.yml` is not enough. Review every row:

| Repository/file | Required changes |
|---|---|
| Engine `databricks.yml` | `domain`, DEV host/warehouse, PROD warehouse, Lakebase name/database, and documented direct-access allowlist. The allowlist variable is not enforcement. |
| Engine `scripts/render.sh` | `DOMAIN` default and any retained sample dashboard/setup filenames and display names. |
| Engine `scripts/build_promote_app.sh` | `APP_DOMAIN`, Steward/Admin emails, content repo, engine URL, reviewer endpoint, slug map, and KA seed. |
| Engine `scripts/provision_ci.sh` | PROD profile/host, SP name, PROD catalog, warehouse, and repository. |
| Engine `scripts/provision_dev_sp.sh` | Prefer explicit `DEV_PROFILE`, `DEV_CATALOG`, warehouse, SP name, scope, and Space IDs. |
| Content workflows | `APP_REPO`, `APP_SPACE_SLUGS`, runner labels, and any domain-specific settings. |
| Content `engine.lock` | Full reviewed engine commit SHA. |
| Content `src/` | Remove the sample for a clean install, or replace every artifact intentionally. |

Add explicit entries to the generated `app.yaml` block in
`scripts/build_promote_app.sh`:

```yaml
  - name: APP_LLM_ENDPOINT
    value: "<prod-reviewer-endpoint>"
  - name: APP_REPO_URL
    value: "https://github.com/<owner>/genie-promote-cicd"
```

For the first deployment:

```yaml
  - name: APP_SPACE_SLUGS
    value: '{}'
  - name: APP_KA_SEED
    value: '[]'
```

Also set `APP_GH_REPO` to the content repository and remove every sample email, endpoint, Space ID,
and repository name from the generated app manifest.

Use one canonical `APP_SPACE_SLUGS` JSON value in both `build_promote_app.sh` and the content
`eval-run` job. An empty clean install uses reversible `s_<space-id>` slugs and needs no pins. If you
choose a friendly pinned slug, the two maps must remain byte-identical; otherwise the live eval job
cannot recover the DEV Space ID and the current script degrades to a green advisory.

Use targeted searches as a review aid, not as an automatic deletion rule:

```bash
rg -n "malcoln|pedro|cerc|recebiveis|genie-spaces-content|01f[0-9a-f]+|ka-[a-z0-9-]+-endpoint" \
  databricks.yml scripts app engine_api .github
```

Test and fixture matches may remain. Deployment manifests, workflows, and provisioning defaults
must be intentional.

### Harden workflows before configuring credentials

Commit these changes while the new repositories have no active Databricks credentials. The first
push may show a failed or unroutable deploy job, but it cannot mutate a workspace:

- remove job-level configuration `if` conditions from the engine `validate`, both content PR jobs,
  and content `deploy-prod`; missing configuration must fail instead of skip;
- add a fail-closed first step to every credentialed job. Check host, client ID, secret, and the
  job's warehouse. `deploy-prod` must also check both `BUNDLE_DEV_*` values before mutation;
- remove `paths-ignore` for Markdown/docs from workflows whose check names will be required. The
  deployment workflow may retain its docs-only filter because it is not a required PR check;
- change the engine/content PROD validation references to the `DATABRICKS_VALIDATION_*` names
  provisioned in step 7; keep `DATABRICKS_PROD_*` only in `deploy-prod`;
- make unresolved eval slugs fail before `check_eval_run.py` runs. Do not accept its current
  unresolved-slug advisory as proof that the required live evaluation executed.

A minimal configuration step can map the job-specific warehouse to `REQUIRED_WAREHOUSE_ID`:

```yaml
- name: Verify required configuration
  shell: bash
  run: |
    required=(DATABRICKS_HOST DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET REQUIRED_WAREHOUSE_ID)
    for name in "${required[@]}"; do
      [ -n "${!name:-}" ] || { echo "::error::$name is not configured"; exit 1; }
    done
```

Set `REQUIRED_WAREHOUSE_ID` from the validation, DEV, or PROD variable appropriate to that job. In
`deploy-prod`, include `BUNDLE_DEV_HOST` and `BUNDLE_DEV_WAREHOUSE_ID` in `required`.

In the content `eval-run` job, add this after the changed-slug and engine-checkout steps:

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

## 7. Prepare Databricks and CI

### Create the data foundation

Create or select:

- `dev_<domain>` and `prod_<domain>` catalogs;
- all referenced schemas/tables;
- one Genie-compatible warehouse per workspace;
- the intended audience's table privileges.

Verify physical access with the identities that will use the data. The promotion pipeline does not
seed or grant production data unless you deliberately retain and run the sample setup job.

### Provision the PROD deployment service principal

`scripts/provision_ci.sh` creates the deployment identity, its baseline grants, an OAuth credential,
and the GitHub Environment reviewer. It is sample-biased and must be edited before use:

- set the PROD profile/host, SP name, catalog, and warehouse;
- create the content repository's `prod` Environment first;
- change its GitHub writes to store `DATABRICKS_PROD_*` as `prod` **Environment** variables/secrets,
  not repository-level values, and pipe the secret to `gh secret set` through stdin;
- run it only for the content repository in the split-identity production model;
- remember that it creates a new OAuth secret on every invocation.

```bash
gh api -X PUT repos/<owner>/genie-spaces-content/environments/prod

# The edited helper's GitHub-write block should have this shape:
gh variable set DATABRICKS_PROD_HOST --repo "$GH_REPO" --env prod --body "$PROD_HOST"
gh variable set DATABRICKS_PROD_SP_CLIENT_ID --repo "$GH_REPO" --env prod --body "$SP_APP_ID"
printf '%s' "$SECRET" |
  gh secret set DATABRICKS_PROD_SP_SECRET --repo "$GH_REPO" --env prod
gh variable set DATABRICKS_PROD_WAREHOUSE_ID --repo "$GH_REPO" --env prod \
  --body "$PROD_WAREHOUSE_ID"
```

After editing the helper:

```bash
PROD_WAREHOUSE_ID=<prod-warehouse-id> \
STEWARD_GH=<steward-github-login> \
GH_REPO=<owner>/genie-spaces-content \
  bash scripts/provision_ci.sh
```

Record the deployment SP application ID and numeric SCIM ID printed by the script. Do not publish
its admin credential to the engine repository or the content repository scope.

The current deployer needs workspace-admin membership for the App/Lakebase binding. Add the CI SP
to PROD `admins` explicitly:

```bash
ADMIN_GROUP_ID="$(databricks groups list -p <prod-profile> \
  --filter 'displayName eq "admins"' -o json |
  python3 -c 'import json,sys; d=json.load(sys.stdin); xs=d if isinstance(d,list) else d.get("Resources",d.get("resources",[])); print(xs[0]["id"])')"

databricks groups patch "$ADMIN_GROUP_ID" -p <prod-profile> --json \
  '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"members","value":[{"value":"<ci-sp-numeric-id>"}]}]}'
```

Revalidate the deployment SP's Unity Catalog privileges against its real duties. It no longer
grants data access to declared audiences. Retain `CREATE_SCHEMA` only if this identity will run the
sample seed, and remove legacy `MANAGE` when no deployment action requires it.

### Provision a separate PR validation service principal

Create a non-admin PROD service principal for engine/content PR validation. It needs workspace
access, `CAN_USE` on the validation warehouse, and enough read access to resolve the PROD tables,
effective grants, users, and groups used by `check_audience.py`; it must not join `admins` or mutate
the app/Lakebase resources.

```bash
VALIDATION_SP_JSON="$(databricks service-principals create \
  --display-name genie-promote-validation-sp -p <prod-profile> -o json)"
VALIDATION_SP_APP_ID="$(printf '%s' "$VALIDATION_SP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["applicationId"])')"
VALIDATION_SP_NUM_ID="$(printf '%s' "$VALIDATION_SP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
unset VALIDATION_SP_JSON

databricks service-principals patch "$VALIDATION_SP_NUM_ID" -p <prod-profile> --json \
  '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"entitlements","value":[{"value":"workspace-access"}]}]}'

databricks warehouses update-permissions <prod-warehouse-id> -p <prod-profile> --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$VALIDATION_SP_APP_ID\",\"permission_level\":\"CAN_USE\"}]}"
databricks grants update catalog prod_<domain> -p <prod-profile> --json \
  "{\"changes\":[{\"principal\":\"$VALIDATION_SP_APP_ID\",\"add\":[\"USE_CATALOG\"]}]}"
```

Grant `USE_SCHEMA`/`SELECT` only where your audience preflight needs to inspect effective access.
Authenticate once as this SP and prove that `users list`, `groups list`, `tables get`, and
`grants get` work for representative objects. If the workspace cannot expose those reads without
admin, the current audience check cannot provide a truly least-privilege split there; keep the
trusted-contributor boundary and treat that as a production gap rather than silently promoting the
validation identity to admin.

Mint one validation credential and write it to both repositories:

```bash
VALIDATION_CRED_JSON="$(databricks service-principal-secrets-proxy create \
  "$VALIDATION_SP_NUM_ID" -p <prod-profile> -o json)"
VALIDATION_SP_SECRET="$(printf '%s' "$VALIDATION_CRED_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
unset VALIDATION_CRED_JSON

for REPO in <owner>/genie-promote-cicd <owner>/genie-spaces-content; do
  gh variable set DATABRICKS_VALIDATION_HOST --repo "$REPO" --body '<prod-workspace-url>'
  gh variable set DATABRICKS_VALIDATION_SP_CLIENT_ID --repo "$REPO" \
    --body "$VALIDATION_SP_APP_ID"
  gh variable set DATABRICKS_VALIDATION_WAREHOUSE_ID --repo "$REPO" \
    --body '<prod-warehouse-id>'
  printf '%s' "$VALIDATION_SP_SECRET" |
    gh secret set DATABRICKS_VALIDATION_SP_SECRET --repo "$REPO"
done
unset VALIDATION_SP_SECRET VALIDATION_SP_APP_ID VALIDATION_SP_NUM_ID REPO
```

In engine `ci.yml` and the content `pr-checks.yml` `validate` job, replace PROD credential
references with `DATABRICKS_VALIDATION_*`. Keep the public job name `bundle validate (prod)` and
continue validating the `prod` bundle target. The content `eval-run` job keeps its DEV identity;
`deploy.yml` keeps `DATABRICKS_PROD_*`, which now resolve only after the `prod` Environment gate.

### Configure repository credentials

Expected GitHub configuration:

| Scope | Variables | Secrets |
|---|---|---|
| Engine repository | `DATABRICKS_VALIDATION_HOST`, `DATABRICKS_VALIDATION_SP_CLIENT_ID`, `DATABRICKS_VALIDATION_WAREHOUSE_ID` | `DATABRICKS_VALIDATION_SP_SECRET` |
| Content repository | all three validation variables, `DATABRICKS_DEV_HOST`, `DATABRICKS_DEV_WAREHOUSE_ID`, `DATABRICKS_DEV_SP_CLIENT_ID` | `DATABRICKS_VALIDATION_SP_SECRET`, `DATABRICKS_DEV_SP_SECRET`, and `ENGINE_REPO_READ_TOKEN` when the engine is private |
| Content `prod` Environment | `DATABRICKS_PROD_HOST`, `DATABRICKS_PROD_SP_CLIENT_ID`, `DATABRICKS_PROD_WAREHOUSE_ID` | `DATABRICKS_PROD_SP_SECRET` |

Only the content repository has the protected `prod` Environment. Confirm no
`DATABRICKS_PROD_SP_SECRET` remains at repository scope.

Set the non-secret DEV values explicitly:

```bash
gh variable set DATABRICKS_DEV_HOST --repo <owner>/genie-spaces-content \
  --body '<dev-workspace-url>'
gh variable set DATABRICKS_DEV_WAREHOUSE_ID --repo <owner>/genie-spaces-content \
  --body '<dev-warehouse-id>'
```

`DATABRICKS_DEV_SP_CLIENT_ID` and `DATABRICKS_DEV_SP_SECRET` are populated after the DEV transport
identity is provisioned in step 10.

Do not enable branch protection yet; first make the checks report successfully once.

### Register runners

Runner dependencies are Git, Bash, Python 3.12, Node/npm, `rsync`, and outbound access to
GitHub/Databricks/PyPI/npm.

For repository-scoped runners, use different installation directories and labels, for example:

```text
~/actions-runner-genie-engine   labels: self-hosted,genie-promote-engine
~/actions-runner-genie-content  labels: self-hosted,genie-promote-content
```

Update `runs-on` in the engine and content workflows to match those labels. An organization runner
group restricted to both repositories is an alternative.

```yaml
# Engine workflow jobs
runs-on: [self-hosted, genie-promote-engine]

# Content workflow jobs
runs-on: [self-hosted, genie-promote-content]
```

Verify that each runner completes a harmless checkout-only job before giving it workspace
credentials. Keep the runner isolated from unrelated workloads.

## 8. Bootstrap the control plane once

Do this **before** the first content deployment. `scripts/deploy_attempt.py` deliberately verifies
that the app and its service principal already exist during preflight.

Run the bootstrap as the same CI service principal used by the workflows. The bundle currently uses
identity-scoped state; bootstrapping as a human and later deploying as the CI SP can create a second
state root and resource conflicts.

Create a short-lived bootstrap secret for the CI SP with the PROD admin profile:

```bash
BOOTSTRAP_JSON="$(databricks service-principal-secrets-proxy create \
  <ci-sp-numeric-id> -p <prod-profile> -o json)"
BOOTSTRAP_SECRET_ID="$(printf '%s' "$BOOTSTRAP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

cleanup_bootstrap() {
  unset DATABRICKS_CLIENT_SECRET DATABRICKS_CLIENT_ID DATABRICKS_AUTH_TYPE DATABRICKS_HOST
  unset DATABRICKS_BUNDLE_ENGINE
  if [ -n "${BOOTSTRAP_SECRET_ID:-}" ]; then
    if ! databricks service-principal-secrets-proxy delete \
      <ci-sp-numeric-id> "$BOOTSTRAP_SECRET_ID" -p <prod-profile>; then
      echo "ERROR: temporary CI credential still exists: $BOOTSTRAP_SECRET_ID" >&2
      return 1
    fi
    unset BOOTSTRAP_SECRET_ID
  fi
}
trap cleanup_bootstrap EXIT

export DATABRICKS_HOST="<prod-workspace-url>"
export DATABRICKS_AUTH_TYPE="oauth-m2m"
export DATABRICKS_CLIENT_ID="<ci-sp-application-id>"
export DATABRICKS_BUNDLE_ENGINE="direct"
export DATABRICKS_CLIENT_SECRET="$(printf '%s' "$BOOTSTRAP_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
unset BOOTSTRAP_JSON
```

From the configured engine repository:

```bash
DOMAIN=<domain> bash scripts/render.sh prod

BUNDLE_DEV_HOST=<dev-workspace-url> \
BUNDLE_DEV_WAREHOUSE_ID=<dev-warehouse-id> \
DATABRICKS_BUNDLE_WAREHOUSE_ID=<prod-warehouse-id> \
  bash scripts/build_promote_app.sh

databricks bundle validate --strict -t prod \
  --var "domain=<domain>" \
  --var "warehouse_id=<prod-warehouse-id>" \
  --var "dev_host=<dev-workspace-url>" \
  --var "dev_warehouse_id=<dev-warehouse-id>" \
  --var "lakebase_instance=<lakebase-name>"

databricks apps deploy -t prod --auto-approve \
  --var "domain=<domain>" \
  --var "warehouse_id=<prod-warehouse-id>" \
  --var "dev_host=<dev-workspace-url>" \
  --var "dev_warehouse_id=<dev-warehouse-id>" \
  --var "lakebase_instance=<lakebase-name>"
```

`databricks apps deploy` is the CLI's Databricks Apps project wrapper: it uses this bundle's root and
identity-scoped state, then starts the app. Steady-state content deployments call `bundle deploy`
through `deploy_attempt.py` against the same root/state. If project deployment is unavailable in a
future CLI, the equivalent bootstrap is `bundle deploy` followed by `bundle run promote_app` under
the same CI identity.

This standalone engine-only deployment is safe **only before the first content deployment**. After
the bundle manages any PROD Space, dashboard, or setup resource, never run a full deploy from an
engine-only checkout: its empty `src/` can be interpreted as an empty desired set and remove managed
content. Every later full deployment must overlay the complete current content repository exactly as
`deploy.yml` does.

Remove the temporary secret even if you retain the longer-lived GitHub Actions credential:

```bash
cleanup_bootstrap
trap - EXIT
```

Verify:

```bash
databricks apps get genie-promote-app -p <prod-profile> -o json
databricks database get-database-instance <lakebase-name> -p <prod-profile> -o json
```

Expected results:

- app state `RUNNING` and a non-empty URL;
- Lakebase state `AVAILABLE`;
- startup logs show successful migrations in the dedicated `genie_promote` schema.

OAuth is required for `databricks apps logs`. A PAT profile can check app status but may not stream
logs.

## 9. Grant the app service principal its runtime access

Resolve the app SP:

```bash
APP_SP="$(databricks apps get genie-promote-app -p <prod-profile> -o json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')"
```

Grant:

- `CAN_USE` on the PROD warehouse;
- `USE_CATALOG` on `prod_<domain>`;
- `USE_SCHEMA` on each schema the app must browse;
- `CAN_QUERY` on the reviewer endpoint;
- `CAN_QUERY` on optional KA endpoints;
- `READ` on the PROD secret scope after it is created.

Examples:

```bash
databricks warehouses update-permissions <prod-warehouse-id> -p <prod-profile> --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$APP_SP\",\"permission_level\":\"CAN_USE\"}]}"

databricks grants update catalog prod_<domain> -p <prod-profile> --json \
  "{\"changes\":[{\"principal\":\"$APP_SP\",\"add\":[\"USE_CATALOG\"]}]}"

databricks grants update schema prod_<domain>.<schema> -p <prod-profile> --json \
  "{\"changes\":[{\"principal\":\"$APP_SP\",\"add\":[\"USE_SCHEMA\"]}]}"

databricks serving-endpoints update-permissions <reviewer-endpoint> -p <prod-profile> --json \
  "{\"access_control_list\":[{\"service_principal_name\":\"$APP_SP\",\"permission_level\":\"CAN_QUERY\"}]}"
```

Grant every intended human persona access to the app. In-app roles remain authoritative for
Steward/Admin capabilities, but Authors, Stewards, and Platform Admins all need workspace-level
`CAN_USE` before they can open it:

```bash
databricks apps update-permissions genie-promote-app -p <prod-profile> --json \
  '{"access_control_list":[
    {"group_name":"<authors-group>","permission_level":"CAN_USE"},
    {"group_name":"<stewards-group>","permission_level":"CAN_USE"},
    {"group_name":"<platform-admins-group>","permission_level":"CAN_USE"}
  ]}'
```

The governed deployment later asserts app-SP `CAN_MANAGE` on every deployed PROD Space.

### Restrict direct Lakebase access

`lakebase_direct_access_admins` records the intended allowlist but is not consumed by the bundle or
any script. Changing it does **not** alter Lakebase permissions.

After bootstrap, use the Lakebase UI and Postgres SQL to audit both permission layers:

1. In the Lakebase project **Settings → Project permissions**, retain only the platform access the
   app and named operators require. Workspace admins retain default project `CAN_MANAGE`.
2. Resolve/create the OAuth Postgres roles for the app SP and each named break-glass admin.
3. With `GRANT`/`REVOKE`, restrict database, schema, table, and sequence access to those roles. The
   app role needs create/read/write access to its `genie_promote` schema; human roles should receive
   only the approved break-glass level.
4. Verify a listed identity can connect and an unlisted non-admin identity cannot read the schema.
   Record that evidence with the installation.

Project ACLs and Postgres database grants are separate and do not synchronize automatically. Follow
the current Databricks guides for
[project permissions](https://docs.databricks.com/aws/en/oltp/projects/manage-project-permissions)
and [database permissions](https://docs.databricks.com/aws/en/oltp/projects/manage-roles-permissions).

## 10. Provision cross-workspace DEV access

Re-resolve the app SP in the current shell; the credential-copy commands below grant it scope
access and must not rely on a variable left over from step 9:

```bash
APP_SP="$(databricks apps get genie-promote-app -p <prod-profile> -o json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')"
[ -n "$APP_SP" ] || { echo "ERROR: app service principal not found"; exit 1; }
```

Create the DEV secret scope before running the helper:

```bash
databricks secrets create-scope genie_promote -p <dev-profile>
```

Then:

```bash
DEV_PROFILE=<dev-profile> \
DEV_CATALOG=dev_<domain> \
DEV_WAREHOUSE_ID=<dev-warehouse-id> \
DEV_SPACE_IDS=<comma-separated-existing-space-ids> \
  bash scripts/provision_dev_sp.sh
```

The helper creates/reuses the DEV transport SP, grants workspace/warehouse/catalog access, applies
`CAN_MANAGE` to the listed existing Spaces, and records its OAuth credential in the DEV scope.

### Replicate the credential

The same client ID/secret must exist in:

1. DEV `genie_promote`, for helper bookkeeping;
2. PROD `genie_promote`, for the running app;
3. the content repository variables/secrets, for live DEV evaluation.

The current helper does not create the PROD copy. An admin CLI may be able to read the DEV secrets:

```bash
CLIENT_ID="$(databricks secrets get-secret genie_promote dev_sp_client_id \
  -p <dev-profile> -o json |
  python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["value"]).decode())')"
CLIENT_SECRET="$(databricks secrets get-secret genie_promote dev_sp_client_secret \
  -p <dev-profile> -o json |
  python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["value"]).decode())')"

databricks secrets create-scope genie_promote -p <prod-profile> 2>/dev/null || true
printf '%s' "$CLIENT_ID" |
  databricks secrets put-secret genie_promote dev_sp_client_id -p <prod-profile>
printf '%s' "$CLIENT_SECRET" |
  databricks secrets put-secret genie_promote dev_sp_client_secret -p <prod-profile>
databricks secrets put-acl genie_promote "$APP_SP" READ -p <prod-profile>

gh variable set DATABRICKS_DEV_SP_CLIENT_ID --repo <owner>/genie-spaces-content \
  --body "$CLIENT_ID"
printf '%s' "$CLIENT_SECRET" |
  gh secret set DATABRICKS_DEV_SP_SECRET --repo <owner>/genie-spaces-content

unset CLIENT_ID CLIENT_SECRET
```

`get-secret` is an undocumented DBUtils-oriented API and can return `BAD_REQUEST` outside a
notebook. If it does, do not copy secrets through logs or files. Mint a replacement credential and
write it to every destination while the one-time value is still in memory:

```bash
DEV_SP_NAME=<chosen-dev-sp-name>
read -r DEV_CLIENT_ID DEV_SP_NUM_ID < <(
  databricks service-principals list -p <dev-profile> -o json |
  python3 -c 'import json,sys; d=json.load(sys.stdin); xs=d if isinstance(d,list) else d.get("Resources",d.get("resources",[])); s=next(x for x in xs if x.get("displayName")==sys.argv[1]); print(s["applicationId"],s["id"])' "$DEV_SP_NAME"
)

DEV_CRED_JSON="$(databricks service-principal-secrets-proxy create \
  "$DEV_SP_NUM_ID" -p <dev-profile> -o json)"
DEV_SECRET_ID="$(printf '%s' "$DEV_CRED_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
DEV_CLIENT_SECRET="$(printf '%s' "$DEV_CRED_JSON" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
unset DEV_CRED_JSON

# DEV bookkeeping used by provision_dev_sp.sh
printf '%s' "$DEV_CLIENT_ID" |
  databricks secrets put-secret genie_promote dev_sp_client_id -p <dev-profile>
printf '%s' "$DEV_CLIENT_SECRET" |
  databricks secrets put-secret genie_promote dev_sp_client_secret -p <dev-profile>
printf '%s' "$DEV_SECRET_ID" |
  databricks secrets put-secret genie_promote dev_sp_secret_id -p <dev-profile>

# PROD runtime
databricks secrets create-scope genie_promote -p <prod-profile> 2>/dev/null || true
printf '%s' "$DEV_CLIENT_ID" |
  databricks secrets put-secret genie_promote dev_sp_client_id -p <prod-profile>
printf '%s' "$DEV_CLIENT_SECRET" |
  databricks secrets put-secret genie_promote dev_sp_client_secret -p <prod-profile>
databricks secrets put-acl genie_promote "$APP_SP" READ -p <prod-profile>

# Content workflow
gh variable set DATABRICKS_DEV_SP_CLIENT_ID --repo <owner>/genie-spaces-content \
  --body "$DEV_CLIENT_ID"
printf '%s' "$DEV_CLIENT_SECRET" |
  gh secret set DATABRICKS_DEV_SP_SECRET --repo <owner>/genie-spaces-content

unset DEV_CLIENT_ID DEV_CLIENT_SECRET DEV_SECRET_ID DEV_SP_NUM_ID DEV_SP_NAME
```

Automate this single-write flow before broad rollout.

Re-run `provision_dev_sp.sh` after a DEV workspace reset and whenever an existing DEV Space is
added to the managed set.

## 11. Configure the GitHub App

Create a GitHub App installed only on the content repository.

Repository permissions:

| Permission | Access |
|---|---|
| Contents | Read and write |
| Pull requests | Read and write |
| Issues | Read and write |
| Checks | Read |
| Actions | Read |
| Administration | Read |
| Metadata | Read (automatic) |

Disable webhooks unless you explicitly implement them. Generate a private key, restrict its local
file permissions, and install the App on the content repository.

After the control plane exists:

```bash
chmod 600 /path/to/github-app.pem
scripts/provision_github_app_secrets.sh \
  <github-app-id> \
  /path/to/github-app.pem \
  <prod-profile> \
  <owner>/genie-spaces-content
```

The helper discovers the installation ID, writes the three PROD scope keys, and attempts to grant
the app SP `READ`. Treat any `WARN` as a failure: the helper currently suppresses some ACL/token
verification errors. It currently passes the PEM through a command argument; on a shared host,
update its `put-secret` calls to read from stdin before use.

Verify that the GitHub App can:

- read the repository;
- create a branch and PR;
- post an issue/PR comment;
- read check runs and annotations;
- read workflow runs and approvals;
- read the `prod` Environment and `main` branch protection.

Delete or archive the PEM securely after storing and verifying it. Rotate it immediately after any
suspected exposure.

## 12. Configure governance controls

### Protected production Environment

On the content repository:

- confirm Environment `prod` exists and holds the deployment-only variables/secret from step 7;
- add the Steward as required reviewer;
- enable `Prevent self-review`;
- prevent untrusted bypass.

Map the same Steward's Databricks email to their GitHub login in the app's role configuration.
Sign in as the bootstrap Admin, open **Administração → Configurações → Papéis
configurados**, choose `Steward`, enter the exact GitHub username, and save. Confirm the assignment
appears and that **Divergência com o GitHub** reports no Steward/Environment mismatch.

### Content branch protection

GitHub cannot require a check context until it has reported at least once. Commit the workflow
hardening in step 6 before adding credentials, then continue step 13 through the first successful PR
checks without merging. Return here and require:

- `bundle validate (prod)`;
- `eval-run pass-rate (dev)`;
- strict, up-to-date checks;
- enforcement for administrators;
- at least one approving PR review;
- no unreviewed bypass actors.

Protect engine `main` as well. At minimum require its `bundle validate (prod)` check and review
engine changes before bumping `engine.lock`. Apply the same always-reporting rule to its required
check.

## 13. Run the first end-to-end promotion

Prepare one DEV Space:

- it references only `dev_<domain>` tables;
- the Author can access it;
- the DEV transport SP has `CAN_MANAGE`;
- any group-based ACL uses an account-level group consistently assigned in DEV and PROD, or the
  Author is granted directly by user;
- it has at least two benchmark questions;
- the intended audience already has its data privileges.

Then:

1. Open the PROD app as an Author.
2. Confirm the Space appears.
3. Run a review and verify the configured reviewer endpoint responds.
4. Request promotion.
5. Verify the content PR contains `serialized_space`, `.title`, `.audience.json`, and any mapping.
6. Verify both required checks pass against the locked engine SHA.
7. Return to step 12 and enable branch protection with both now-visible check contexts.
8. Obtain the required PR approval and have the authorized reviewer/merger merge the PR.
9. Have a different GitHub actor, assigned as Steward, approve the `prod` Environment. With
   `Prevent self-review`, the actor who triggered the deployment cannot also approve it.
10. Verify the deployment completes all stages: preflight, bundle deploy, Space resolution,
   app `CAN_MANAGE`, audience reconciliation, and live readback.
11. Verify the app shows the completed audit timeline and the PROD Space has the expected content
    and `CAN_RUN` audience.

The installation is ready only when this succeeds without a human Databricks token in CI.

## 14. Optional features

### Knowledge Assistants

Create and activate the Agent Bricks KAs, grant the app SP `CAN_QUERY` on each serving endpoint,
then add a customer-specific `APP_KA_SEED`. KA findings are advisory and never block promotion.

KA endpoints use the Responses API shape at `/invocations`, not Chat Completions.

### Sample data

The sample content's `src/setup/seed_recebiveis.py` becomes a bundle `setup` job only after the
content is overlaid and rendered. Deployment does not run it. Review it, deploy it intentionally to
the correct target, and invoke `bundle run setup` only in a disposable/demo installation.

### Reset demo history

Preview first:

```bash
python3 scripts/reset_demo_ledger.py --profile <prod-profile>
```

Execute only in the intended demo installation:

```bash
python3 scripts/reset_demo_ledger.py --profile <prod-profile> \
  --execute --confirm DELETE-DEMO-LEDGER
```

This preserves roles, rules, reviewer prompt, and KA configuration.

## 15. Day-2 operations

### Upgrade the engine

1. Merge and validate the engine change.
2. Verify the commit is reachable from protected engine `main` or a trusted immutable release.
3. Copy its full commit SHA into content `engine.lock`.
4. Open a content PR containing only the reviewed lock bump.
5. Run both content checks against that SHA.
6. Merge and approve deployment through the normal gate.

Never point workflows at a floating engine branch for production.

### Recover after a DEV reset

1. Recreate/bind the DEV catalog, warehouse, users/groups, and Spaces. Record any new workspace URL,
   warehouse ID, and Space IDs.
2. If the host or warehouse changed, update `DATABRICKS_DEV_HOST` and
   `DATABRICKS_DEV_WAREHOUSE_ID` in the content repository.
3. Trigger a governed **content-repository** deployment so the workflow overlays the complete
   current `src/` before rebuilding the app. If no engine/content artifact changed, update a tracked
   non-documentation marker such as `.deploy-trigger` in a reviewed PR; its merge runs `deploy.yml`
   with the new variables and the protected `prod` Environment. This refreshes baked
   `APP_DEV_HOST`/`APP_DEV_WAREHOUSE_ID`; changing GitHub variables alone does not.
4. Re-run `provision_dev_sp.sh` with the current Space IDs and replicate a newly minted secret only
   if the helper reports rotation.
5. If custom pinned Space IDs changed, update the canonical `APP_SPACE_SLUGS` in both the app build
   and content eval workflow, then redeploy the app through the reviewed engine/lock process.
6. Prove the app reports the new DEV host/warehouse and re-run the live DEV eval check before
   accepting promotions.

### Rotate credentials

- PROD deployment SP: create a new secret, update the content `prod` Environment secret, prove one
  gated deployment, then delete the old secret.
- PROD validation SP: create a new secret, update both repository-level validation secrets, prove
  both PR workflows, then delete the old secret.
- DEV transport SP: write the same new credential to DEV bookkeeping, PROD runtime, and content
  GitHub in one controlled operation.
- GitHub App: generate/install the new PEM, update the PROD scope, verify token minting, then revoke
  the old key.

### App recreation

Recreating the Databricks App rotates its service principal. Re-resolve `APP_SP` and reapply:

- PROD secret-scope `READ`;
- warehouse `CAN_USE`;
- catalog/schema privileges;
- reviewer/KA `CAN_QUERY`;
- `CAN_MANAGE` on existing governed Spaces;
- human persona/group `CAN_USE` on the recreated app.

Do not recreate or redeploy it from a standalone engine checkout after content is managed. Use a
normal gated content deployment. If the app is already missing and preflight cannot start, perform
the step-8 bootstrap authentication but first check out the exact `engine.lock` revision, overlay the
complete content repository `src/`, render it, and inspect the desired resource set before running
`apps deploy`. An engine-only recovery is destructive.

## 16. Troubleshooting

| Symptom | Likely cause / action |
|---|---|
| Content deployment fails before mutation with missing app | The one-time CI-SP control-plane bootstrap was skipped. |
| Content workflow is skipped | The sample job-level `if` or path filters were not removed. Harden the required jobs as described in step 12. |
| Cross-repo checkout fails | `APP_REPO` is wrong, `engine.lock` is unavailable, or a private engine checkout token is missing. |
| Lock/readiness mismatch | Update `engine.lock` through a reviewed content PR. |
| App deploy fails at Lakebase binding | Confirm the deploying CI SP is a PROD workspace admin. |
| App is deployed but stopped | Use `databricks apps deploy` or `bundle run promote_app`. |
| App deploy reports user-token passthrough disabled | Enable Databricks Apps user authorization for `dashboards.genie`. |
| Review fails at Model Serving | Set `APP_LLM_ENDPOINT`, verify endpoint availability, and grant app-SP `CAN_QUERY`. |
| DEV Space is missing | Re-run DEV bootstrap with its ID; verify Author and transport-SP Space ACLs. |
| `DevEnvironmentNotBootstrapped` | DEV credentials are missing/stale in the PROD scope or DEV grants were lost. |
| GitHub drift is unknown | Add `Checks: read` and `Administration: read` to the GitHub App and reinstall/approve changes. |
| Required check never appears | Confirm the required workflow has no matching `paths-ignore`, all jobs fail rather than skip on missing config, and the labeled runner is online. |
| Lakebase schema permission denied | A human likely created the app schema first. Preserve data, then restore ownership; do not drop it casually. |
| Runner job hangs/fails installing | Verify Node/npm, Python, PyPI/npm egress, disk, and unique runner routing. |

For final pilot acceptance, complete [docs/PILOT-GO-NO-GO.md](docs/PILOT-GO-NO-GO.md) and store a
redacted evidence manifest outside the repository.

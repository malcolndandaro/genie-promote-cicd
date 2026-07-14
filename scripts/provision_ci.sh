#!/usr/bin/env bash
# provision_ci.sh — one-time CI/CD provisioning for the Acme Genie pipeline (S3).
#
# Idempotent-ish. Run ONCE after creating the GitHub repo. It:
#   1. creates (or reuses) the per-domain CI/CD service principal in prod,
#   2. grants it exactly what it needs to deploy the bundle (least privilege),
#   3. mints an OAuth M2M secret,
#   4. pushes host + client-id (GitHub *variables*) and the secret (GitHub *secret*),
#   5. creates the `prod` Environment with a required reviewer AND prevent-self-review
#      (enforces Requester != Approver — ADR-0002 SoD; reviewers alone do NOT),
#   6. prints the self-hosted runner registration command.
#
# Portable (ADR-0004): everything is parameterized below — workflows read vars/secrets,
# never literals. Prereqs: workspace admin on prod; `gh` authed (repo scope); the
# prod_recebiveis catalog exists (S1).
set -euo pipefail

# ---- config (edit for a different workspace pair / repo) --------------------
PROD_PROFILE="databricks-prod"
PROD_HOST="https://your-prod-workspace.cloud.databricks.com"
GH_REPO="${GH_REPO:?set GH_REPO=owner/name}"          # e.g. malcolndandaro/genie-promote
SP_NAME="genie-promote-prod-sp"
DOMAIN_CATALOG="prod_recebiveis"
PROD_WAREHOUSE_ID="${PROD_WAREHOUSE_ID:-your-prod-warehouse-id}"  # set to your prod SQL warehouse id
STEWARD_GH="${STEWARD_GH:-}"                            # GitHub *login* of the Steward (required reviewer)

# ---- 1. create (or find) the service principal ------------------------------
# Capture BOTH ids: applicationId (UUID, for UC grants) and the numeric SCIM id
# (for the secrets API). They are NOT interchangeable (B1).
read -r SP_APP_ID SP_NUM_ID < <(databricks service-principals list -p "$PROD_PROFILE" -o json \
  | python3 -c "import json,sys;d=json.load(sys.stdin);L=d if isinstance(d,list) else d.get('Resources',d.get('resources',[]));m=next((s for s in L if s.get('displayName')=='$SP_NAME'),None);print((m or {}).get('applicationId',''),(m or {}).get('id',''))")
if [ -z "$SP_APP_ID" ]; then
  read -r SP_APP_ID SP_NUM_ID < <(databricks service-principals create --display-name "$SP_NAME" -p "$PROD_PROFILE" -o json \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['applicationId'],d['id'])")
  echo "created SP $SP_NAME -> app=$SP_APP_ID num=$SP_NUM_ID"
else
  echo "reusing SP $SP_NAME -> app=$SP_APP_ID num=$SP_NUM_ID"
fi

# ---- 2. least-privilege grants (use applicationId for UC) -------------------
# MANAGE: the pipeline's apply_access step GRANTs USE/SELECT to declared principals on this
# catalog's securables — granting to others requires MANAGE on the catalog (found live 2026-07-14:
# PermissionDenied without it; workspace-admin does NOT imply UC MANAGE).
databricks grants update catalog "$DOMAIN_CATALOG" -p "$PROD_PROFILE" \
  --json "{\"changes\":[{\"principal\":\"$SP_APP_ID\",\"add\":[\"USE_CATALOG\",\"USE_SCHEMA\",\"CREATE_SCHEMA\",\"MANAGE\"]}]}"
# Warehouse CAN_USE — additive update (set-permissions would REPLACE the whole ACL). M2.
databricks warehouses update-permissions "$PROD_WAREHOUSE_ID" -p "$PROD_PROFILE" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP_APP_ID\",\"permission_level\":\"CAN_USE\"}]}"

# ---- 3. OAuth M2M secret (numeric SCIM id, NOT applicationId) ---------------
SECRET=$(databricks service-principal-secrets-proxy create "$SP_NUM_ID" -p "$PROD_PROFILE" -o json \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['secret'])")

# ---- 4. push to GitHub (variables = non-secret, secret = the OAuth secret) ---
gh variable set DATABRICKS_PROD_HOST          --repo "$GH_REPO" --body "$PROD_HOST"
gh variable set DATABRICKS_PROD_SP_CLIENT_ID  --repo "$GH_REPO" --body "$SP_APP_ID"
gh secret   set DATABRICKS_PROD_SP_SECRET     --repo "$GH_REPO" --body "$SECRET"
gh variable set DATABRICKS_PROD_WAREHOUSE_ID  --repo "$GH_REPO" --body "$PROD_WAREHOUSE_ID"

# ---- 5. prod Environment: required reviewer + PREVENT SELF-REVIEW (SoD) ------
# prevent_self_review=true is what actually enforces Requester != Approver. Without
# it, the PR author can approve their own deploy (ADR-0002 violation). B2.
if [ -n "$STEWARD_GH" ]; then
  REVIEWER_ID=$(gh api "users/$STEWARD_GH" --jq .id)
  gh api -X PUT "repos/$GH_REPO/environments/prod" \
    -F "prevent_self_review=true" \
    -f "reviewers[][type]=User" -F "reviewers[][id]=$REVIEWER_ID"
  echo "prod environment: reviewer=$STEWARD_GH, prevent_self_review=true"
else
  echo "!! set STEWARD_GH=<github-login> to wire the required reviewer, OR set it in"
  echo "   repo Settings > Environments > prod (add reviewer + tick 'Prevent self-review')."
fi
echo ">> Note: prevent-self-review only blocks self-approval across DISTINCT GitHub accounts."
echo "   The PR author and the approving reviewer must be different GitHub logins."

# ---- 6. self-hosted runner (workspace IP-ACLs block hosted runners) ---------
# NB: the release asset name is versioned; the bare 'latest/download/...tar.gz' 404s.
# This fetches the current version. (osx-arm64 shown; use linux-x64 on a Linux host.)
echo ">> Register the self-hosted runner (scope it to THIS repo only):"
echo "   VER=\$(gh api repos/actions/runner/releases/latest --jq .tag_name | sed 's/^v//')"
echo "   mkdir -p ~/actions-runner && cd ~/actions-runner"
echo "   curl -L -o r.tar.gz https://github.com/actions/runner/releases/download/v\${VER}/actions-runner-osx-arm64-\${VER}.tar.gz && tar xzf r.tar.gz"
echo "   TOKEN=\$(gh api -X POST repos/$GH_REPO/actions/runners/registration-token --jq .token)"
echo "   ./config.sh --url https://github.com/$GH_REPO --token \$TOKEN --labels self-hosted --unattended"
echo "   ./svc.sh install && ./svc.sh start   # persistent service (survives logout)"
echo ""
echo "Done. Push to main -> deploy-prod waits on the prod gate -> Steward approves -> SP deploys."

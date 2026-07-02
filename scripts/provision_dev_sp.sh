#!/usr/bin/env bash
# provision_dev_sp.sh — idempotent SELF-HEAL bootstrap for the dev-reader/writer service principal
# (A2, per SP2's dev-wipe blast-radius finding). Unlike provision_ci.sh (a mostly one-time CI SP
# bootstrap), this script is meant to be RE-RUN — on first setup AND after every dev workspace
# wipe/redeploy — because SP2 found that workspace-LOCAL state (workspace access assignment,
# warehouse/Genie permissions, the dev_recebiveis workspace<->catalog binding) does not reliably
# survive a dev wipe even though the SP's ACCOUNT-level identity + OAuth secret do.
#
# What it does, EVERY run (additive/idempotent — safe to re-run when nothing changed):
#   1. reuse-or-create the dev-reader/writer SP (a plain team-created account SP, NEVER an
#      FEVM-minted identity — see SP2: FEVM may reap its own SPs on teardown, which would silently
#      break the "account-level identity survives" assumption this whole design leans on).
#   2. re-assert workspace access (the `workspace-access` entitlement) — additive PATCH, not a
#      destructive replace.
#   3. re-assert warehouse CAN_USE (Genie requires at least CAN_USE on a Pro/Serverless warehouse).
#   4. re-assert the Genie ACL (CAN_MANAGE) on every KNOWN dev Space (config-driven list) — the
#      workspace-level entitlement alone is enough to CREATE new spaces, but reading/overwriting an
#      EXISTING space via `get_space(include_serialized_space=True)`/`update_space` needs a
#      per-Space ACL grant (SP1: "Requires at least CAN EDIT permission on the space" — this script
#      grants the stronger CAN_MANAGE so the SP can also update/re-title on overwrite; A2's own
#      `assert_can_access` guard is the thing that actually decides which USER may act on a given
#      Space — this per-Space grant only gives the SP itself platform-level reach, same as
#      `genie-workbench`'s existing CAN_MANAGE entries observed live in dev).
#   5. re-assert the `dev_recebiveis` workspace<->catalog binding + UC grants (mirrors
#      src/setup/seed_recebiveis.py's own best-effort binding step, so a severed binding after a
#      full teardown+redeploy is repaired the same way the seed job repairs it for itself).
#   6. mint an OAuth M2M secret ONLY IF the currently-stored one is no longer valid (SP2's
#      "never mint-on-every-run" gotcha — provision_ci.sh mints unconditionally, which would
#      silently invalidate the previously-stored secret on every self-heal run of THIS script; a
#      weekly self-heal cadence makes that a real operational trap, not a one-time inconvenience).
#      Validity is checked WITHOUT ever reading the secret's value back (the API never returns it):
#      the secret's `secret_id` is stored alongside the client id/secret in the Databricks secret
#      scope, and re-validated on each run against `service-principal-secrets-proxy list` — if that
#      id no longer appears in the SP's live secret list (rotated/deleted out-of-band, or first
#      run), a fresh secret is minted; otherwise the existing one is left untouched.
#
# Config-driven (ADR-0004): NOTHING below is hardcoded to this customer/workspace beyond the
# defaults, which are all overridable via environment variables. A second customer sets
# DEV_PROFILE/DEV_HOST/DEV_CATALOG/DEV_WAREHOUSE_ID/DEV_SPACE_IDS/etc. and re-runs this script —
# no code change.
#
# Deploy ordering (ADR-0006 Decision 5 / this script's own contract):
#   - This script targets the DEV workspace and is INDEPENDENT of the prod app/database_instances
#     bootstrap order — it unlocks A3 (rehydrate)/the dev-sp client path, not A1's app-in-prod
#     milestone. Run it: (a) once, before the dev-sp path is first exercised live; (b) again after
#     any dev workspace wipe/redeploy (SP2's worst-case model); (c) whenever a NEW dev Genie Space
#     needs the SP's reach added to DEV_SPACE_IDS.
#   - After minting/rotating the secret, the app must be able to read the (possibly new) secret
#     values on its next request — no redeploy is required (app_logic._dev_sp_client reads the
#     scope live via app_logic._secret at CALL time, not at deploy time).
#
# Prereqs: `databricks` CLI + `python3` on PATH; DEV_PROFILE authenticated with enough rights to
# create/patch service principals, grant UC privileges, manage catalog bindings, and read/write the
# target secret scope (typically a workspace/account admin identity — this script itself is NOT
# the dev SP, it's what PROVISIONS the dev SP).
set -euo pipefail

# ---- config (all overridable — ADR-0004; nothing here is a customer-specific literal) --------
DEV_PROFILE="${DEV_PROFILE:-cerc-mlops-dev}"
DEV_CATALOG="${DEV_CATALOG:-dev_recebiveis}"
DEV_WAREHOUSE_ID="${DEV_WAREHOUSE_ID:?set DEV_WAREHOUSE_ID=<dev sql warehouse id>}"
SP_NAME="${DEV_SP_NAME:-genie-promote-dev-sp}"
SECRET_SCOPE="${DEV_SP_SECRET_SCOPE:-genie_promote}"
# Comma-separated dev Genie Space ids the SP needs per-Space CAN_MANAGE on (existing Spaces it must
# read/overwrite — e.g. the canonical demo space + any space A3/rehydrate targets). A brand-new
# Space the SP CREATES needs no pre-existing grant (create mints a fresh ACL owned by the creator).
DEV_SPACE_IDS="${DEV_SPACE_IDS:-}"

echo "== provision_dev_sp.sh: profile=$DEV_PROFILE sp=$SP_NAME catalog=$DEV_CATALOG =="

# ---- 1. reuse-or-create the SP (mirrors provision_ci.sh; capture BOTH ids) --------------------
# NOTE: this must be a plain team-created account SP (like this SP itself, or `cerc-mlops-cicd`)
# — NEVER one of the FEVM-managed identities (`vending-machine-sp`, `fevm-sage-sp`) observed live
# in dev. Nothing in this script can enforce that (SP creation here always makes a plain SP), but
# operators must not repoint DEV_SP_NAME at an FEVM identity.
read -r SP_APP_ID SP_NUM_ID < <(databricks service-principals list -p "$DEV_PROFILE" -o json \
  | python3 -c "import json,sys;d=json.load(sys.stdin);L=d if isinstance(d,list) else d.get('Resources',d.get('resources',[]));m=next((s for s in L if s.get('displayName')=='$SP_NAME'),None);print((m or {}).get('applicationId',''),(m or {}).get('id',''))")
if [ -z "$SP_APP_ID" ]; then
  read -r SP_APP_ID SP_NUM_ID < <(databricks service-principals create --display-name "$SP_NAME" -p "$DEV_PROFILE" -o json \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['applicationId'],d['id'])")
  echo "created dev SP $SP_NAME -> app=$SP_APP_ID num=$SP_NUM_ID"
else
  echo "reusing dev SP $SP_NAME -> app=$SP_APP_ID num=$SP_NUM_ID"
fi

# ---- 2. re-assert workspace access (additive SCIM PATCH, not a destructive replace) -----------
# `workspace-access` is the standard entitlement observed live on existing app SPs in this
# workspace (genie-promote-app, genie-workbench) — it's what lets an SP authenticate + call
# workspace APIs at all. PATCH with op=add is idempotent: re-adding an entitlement that is already
# present is a no-op, unlike `service-principals update` (a full replace) which would risk
# clobbering entitlements this script doesn't know about.
databricks service-principals patch "$SP_NUM_ID" -p "$DEV_PROFILE" --json '{
  "Operations": [{"op": "add", "path": "entitlements", "value": [{"value": "workspace-access"}]}]
}'
echo "workspace-access entitlement asserted"

# ---- 3. re-assert warehouse CAN_USE (Genie requires >= CAN_USE on the warehouse) --------------
# Additive update (mirrors provision_ci.sh's own M2 gotcha comment): `update-permissions` merges,
# `set-permissions` would REPLACE the whole ACL and could strip an unrelated grant.
databricks warehouses update-permissions "$DEV_WAREHOUSE_ID" -p "$DEV_PROFILE" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP_APP_ID\",\"permission_level\":\"CAN_USE\"}]}"
echo "warehouse CAN_USE asserted on $DEV_WAREHOUSE_ID"

# ---- 4. re-assert per-Space Genie ACLs (CAN_MANAGE) on every KNOWN space ----------------------
# `permissions update genie <space_id>` is an ADDITIVE update (unlike `permissions set`, which
# replaces the whole ACL) — safe to re-run, and it will never remove another principal's grant.
if [ -n "$DEV_SPACE_IDS" ]; then
  IFS=',' read -ra SPACE_ID_ARRAY <<< "$DEV_SPACE_IDS"
  for space_id in "${SPACE_ID_ARRAY[@]}"; do
    space_id="$(echo "$space_id" | xargs)"  # trim whitespace
    [ -z "$space_id" ] && continue
    databricks permissions update genie "$space_id" -p "$DEV_PROFILE" --json \
      "{\"access_control_list\":[{\"service_principal_name\":\"$SP_APP_ID\",\"permission_level\":\"CAN_MANAGE\"}]}"
    echo "genie CAN_MANAGE asserted on space $space_id"
  done
else
  echo "DEV_SPACE_IDS is empty — skipping per-Space ACL grants (the SP can still CREATE new" \
       "Spaces via workspace-access + warehouse CAN_USE; add existing Space ids here once known)."
fi

# ---- 5. re-assert the dev_recebiveis workspace<->catalog binding + UC grants ------------------
# Mirrors src/setup/seed_recebiveis.py's own best-effort binding step exactly (same API shape),
# so a severed binding after a full teardown+redeploy (SP2's worst-case model) is repaired the
# same way the seed job repairs it for its own caller. Also grants the SP the same baseline UC
# privileges provision_ci.sh grants the prod CI SP (USE_CATALOG/USE_SCHEMA), since the SP's Genie
# exports/rebinds reference dev_recebiveis-qualified tables.
databricks grants update catalog "$DEV_CATALOG" -p "$DEV_PROFILE" \
  --json "{\"changes\":[{\"principal\":\"$SP_APP_ID\",\"add\":[\"USE_CATALOG\",\"USE_SCHEMA\"]}]}"
echo "UC catalog grants asserted on $DEV_CATALOG"

python3 - "$DEV_PROFILE" "$DEV_CATALOG" <<'PYEOF'
import sys
from databricks.sdk import WorkspaceClient

profile, catalog = sys.argv[1], sys.argv[2]
w = WorkspaceClient(profile=profile)
try:
    ws_id = w.get_workspace_id()
    w.api_client.do("PATCH", f"/api/2.1/unity-catalog/catalogs/{catalog}",
                    body={"isolation_mode": "ISOLATED"})
    w.api_client.do("PATCH", f"/api/2.1/unity-catalog/bindings/catalog/{catalog}",
                    body={"add": [{"workspace_id": ws_id, "binding_type": "BINDING_TYPE_READ_WRITE"}]})
    print(f"catalog {catalog} ISOLATED + bound to workspace {ws_id}")
except Exception as e:  # noqa: BLE001 — binding is best-effort, matches seed_recebiveis.py
    print(f"[warn] binding reassertion skipped: {e}")
PYEOF

# ---- 6. mint an OAuth secret ONLY IF the stored one is no longer valid ------------------------
# SP2's core gotcha: provision_ci.sh mints unconditionally, which is fine for a one-time bootstrap
# but wrong for a script meant to self-heal on every dev wipe. We track WHICH secret_id backs the
# currently-stored credential (dev_sp_secret_id) and only mint a replacement when that id no
# longer shows up in the SP's live secret list (rotated/deleted out-of-band, or genuinely first
# run) — never on a routine no-op re-run.
CURRENT_SECRET_ID=""
if databricks secrets get-secret "$SECRET_SCOPE" dev_sp_secret_id -p "$DEV_PROFILE" -o json >/tmp/dev_sp_secret_id.json 2>/dev/null; then
  CURRENT_SECRET_ID=$(python3 -c "import json,base64;print(base64.b64decode(json.load(open('/tmp/dev_sp_secret_id.json'))['value']).decode())" 2>/dev/null || true)
fi
rm -f /tmp/dev_sp_secret_id.json

STILL_VALID="false"
if [ -n "$CURRENT_SECRET_ID" ]; then
  LIVE_IDS=$(databricks service-principal-secrets-proxy list "$SP_NUM_ID" -p "$DEV_PROFILE" -o json \
    | python3 -c "import json,sys;d=json.load(sys.stdin);L=d if isinstance(d,list) else d.get('secrets',[]);print('\n'.join(s.get('id','') for s in L))")
  if grep -qxF "$CURRENT_SECRET_ID" <<< "$LIVE_IDS"; then
    STILL_VALID="true"
  fi
fi

if [ "$STILL_VALID" = "true" ]; then
  echo "existing dev SP secret ($CURRENT_SECRET_ID) is still valid — NOT minting a new one"
else
  echo "no valid stored secret found — minting a new OAuth M2M secret"
  read -r NEW_SECRET_ID NEW_SECRET < <(databricks service-principal-secrets-proxy create "$SP_NUM_ID" -p "$DEV_PROFILE" -o json \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['id'],d['secret'])")
  databricks secrets put-secret "$SECRET_SCOPE" dev_sp_client_id --string-value "$SP_APP_ID" -p "$DEV_PROFILE"
  databricks secrets put-secret "$SECRET_SCOPE" dev_sp_client_secret --string-value "$NEW_SECRET" -p "$DEV_PROFILE"
  databricks secrets put-secret "$SECRET_SCOPE" dev_sp_secret_id --string-value "$NEW_SECRET_ID" -p "$DEV_PROFILE"
  echo "new dev SP secret minted + stored in scope '$SECRET_SCOPE' (id=$NEW_SECRET_ID)"
fi

echo ""
echo "== done =="
echo "The app (scope=\"dev-sp\") reads client_id/client_secret from the '$SECRET_SCOPE' scope at"
echo "CALL time (app/app_logic.py::_dev_sp_client) — no app redeploy needed after this script runs."
echo "Re-run this script: (a) after any dev workspace wipe/redeploy; (b) whenever a new dev Genie"
echo "Space needs the SP's reach added to DEV_SPACE_IDS; (c) on a routine schedule if you want"
echo "self-heal without waiting for a rehydrate/read failure to surface staleness."
echo ""
echo ">> AUDIT/ANOMALY MONITORING (per the PRD — the SP is inherently broad: Genie has no"
echo "   per-Space delegation, so this SP can reach EVERY dev Space by platform necessity):"
echo "   - Treat $SP_NAME as a high-value target. Alert on: sign-ins/API calls from this SP"
echo "     outside the app's expected call pattern, secret creation/rotation events on it, and"
echo "     any entitlement/permission change to it that this script did not just make."
echo "   - Databricks account-level audit logs (system.access.audit or the account console) record"
echo "     every action BY this SP's identity — cross-reference against app_logic's own audit"
echo "     trail (authz.verify_identity's caller), which separately records the acting HUMAN"
echo "     identity for every dev-touching action, so an incident review can tell \"a verified"
echo "     user triggered this via the SP\" from \"the SP was misused directly\" (ADR-0006)."

#!/usr/bin/env bash
# provision_github_app_secrets.sh — put the genie-promote-bot GitHub App creds into the prod
# `genie_promote` secret scope so the app can open promotion PRs (task #10 of the 2026-07-15 prod
# migration). The app (app/app_logic.py, GH_SECRET_SCOPE default "genie_promote") reads three keys:
#   github_app_id · github_installation_id · github_app_private_key
#
# The ONLY inputs a human must supply are the App's PRIVATE-KEY .pem and its numeric App ID — both
# come from the GitHub App settings page (Settings → Developer settings → GitHub Apps →
# genie-promote-bot). Everything else (the installation_id on the content repo, and the READ ACL for
# the app SP) is DERIVED here, so this stays correct even though the installation_id CHANGES whenever
# the App's repo list changes (e.g. after adding genie-spaces-content to the install — the migration's
# task #7). That auto-discovery is why this script exists instead of three hand-run put-secret calls
# with a stale, manually-copied installation_id.
#
# Prereqs: `databricks` CLI authed to the prod profile; python3 with PyJWT (`pip install pyjwt`) OR
# openssl on PATH (the script falls back to a pure-openssl RS256 signer); the App already INSTALLED
# on the target repo (task #7 — a browser action this script cannot perform).
#
# Usage:
#   scripts/provision_github_app_secrets.sh <app-id> <path/to/private-key.pem> [prod-profile] [target-repo]
# Example (this migration):
#   scripts/provision_github_app_secrets.sh 123456 ~/Downloads/genie-promote-bot.private-key.pem \
#       malcoln-aws-stable malcolndandaro/genie-spaces-content
set -euo pipefail

APP_ID="${1:?usage: provision_github_app_secrets.sh <app-id> <private-key.pem> [prod-profile] [target-repo]}"
PEM_PATH="${2:?missing <private-key.pem> path}"
PROD_PROFILE="${3:-malcoln-aws-stable}"
TARGET_REPO="${4:-malcolndandaro/genie-spaces-content}"
SCOPE="${APP_GH_SECRET_SCOPE:-genie_promote}"

[ -f "$PEM_PATH" ] || { echo "ERROR: PEM not found at $PEM_PATH" >&2; exit 1; }

echo "App ID          : $APP_ID"
echo "PEM             : $PEM_PATH"
echo "Prod profile    : $PROD_PROFILE"
echo "Target repo     : $TARGET_REPO"
echo "Secret scope    : $SCOPE"
echo

# ---- 1. Mint a short-lived App JWT (RS256, iss=App ID, exp<=10min) ---------------------------
# Prefer PyJWT; fall back to a portable openssl signer so this runs on a bare box.
mint_jwt() {
  python3 - "$APP_ID" "$PEM_PATH" <<'PY' 2>/dev/null || return 1
import sys, time, jwt   # PyJWT
app_id, pem_path = sys.argv[1], sys.argv[2]
key = open(pem_path).read()
now = int(time.time())
tok = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, key, algorithm="RS256")
print(tok if isinstance(tok, str) else tok.decode())
PY
}
mint_jwt_openssl() {
  # Pure openssl RS256 fallback (no PyJWT). base64url header/payload, sign, base64url signature.
  local now iat exp header payload signing_input sig
  now=$(date +%s); iat=$((now - 60)); exp=$((now + 540))
  b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
  header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
  payload=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$iat" "$exp" "$APP_ID" | b64url)
  signing_input="${header}.${payload}"
  sig=$(printf '%s' "$signing_input" | openssl dgst -sha256 -sign "$PEM_PATH" | b64url)
  printf '%s.%s' "$signing_input" "$sig"
}

echo "==> minting App JWT"
JWT="$(mint_jwt || true)"
if [ -z "${JWT:-}" ]; then
  echo "    PyJWT unavailable; using openssl fallback"
  JWT="$(mint_jwt_openssl)"
fi
[ -n "$JWT" ] || { echo "ERROR: could not mint JWT (need PyJWT or openssl)" >&2; exit 1; }

# ---- 2. Discover the installation id for the TARGET repo -------------------------------------
# GET /repos/{owner}/{repo}/installation as the App returns the installation covering that repo —
# so we never hand-copy an installation_id (and it survives the task-#7 repo-add that changes it).
echo "==> discovering installation id for $TARGET_REPO"
INSTALL_JSON="$(curl -sf -H "Authorization: Bearer $JWT" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/${TARGET_REPO}/installation")" || {
  echo "ERROR: GET /repos/${TARGET_REPO}/installation failed — is the App INSTALLED on that repo (task #7)?" >&2
  echo "       (also verify the App ID + PEM belong to genie-promote-bot)" >&2
  exit 1
}
INSTALL_ID="$(printf '%s' "$INSTALL_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
[ -n "$INSTALL_ID" ] || { echo "ERROR: no installation id in response" >&2; exit 1; }
echo "    installation id = $INSTALL_ID"

# ---- 3. Put the three keys into the prod scope (create scope if missing) ---------------------
echo "==> writing secrets to scope '$SCOPE' on profile '$PROD_PROFILE'"
databricks secrets create-scope "$SCOPE" -p "$PROD_PROFILE" 2>/dev/null || true  # no-op if exists
databricks secrets put-secret "$SCOPE" github_app_id          --string-value "$APP_ID"          -p "$PROD_PROFILE"
databricks secrets put-secret "$SCOPE" github_installation_id --string-value "$INSTALL_ID"       -p "$PROD_PROFILE"
databricks secrets put-secret "$SCOPE" github_app_private_key --string-value "$(cat "$PEM_PATH")" -p "$PROD_PROFILE"
echo "    put github_app_id / github_installation_id / github_app_private_key"

# ---- 4. Ensure the app SP can READ the scope -------------------------------------------------
# The app reads these as ITS OWN SP. Resolve the app SP dynamically (survives app-recreation SP
# rotation — the 2026-07-15 migration learned this the hard way) and grant READ.
APP_SP="$(databricks apps get genie-promote-app -p "$PROD_PROFILE" -o json 2>/dev/null \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("service_principal_client_id",""))' 2>/dev/null || true)"
if [ -n "$APP_SP" ]; then
  databricks secrets put-acl "$SCOPE" "$APP_SP" READ -p "$PROD_PROFILE" 2>/dev/null \
    && echo "    granted READ on '$SCOPE' to app SP $APP_SP" \
    || echo "    (app SP $APP_SP already has an ACL, or put-acl skipped)"
else
  echo "    WARN: could not resolve the app SP — grant READ manually:"
  echo "          databricks secrets put-acl $SCOPE <app-sp-client-id> READ -p $PROD_PROFILE"
fi

# ---- 5. Verify the bot authenticates ---------------------------------------------------------
echo "==> verifying: mint installation token"
if curl -sf -X POST -H "Authorization: Bearer $JWT" -H "Accept: application/vnd.github+json" \
     "https://api.github.com/app/installations/${INSTALL_ID}/access_tokens" >/dev/null; then
  echo "    OK — the App can mint an installation token for $TARGET_REPO."
else
  echo "    WARN: installation-token mint failed — check the App's repo permissions (needs contents+PRs write)."
fi

echo
echo "DONE. Restart the app so it picks up the new secrets:"
echo "  databricks apps stop  genie-promote-app -p $PROD_PROFILE"
echo "  databricks apps start genie-promote-app -p $PROD_PROFILE"

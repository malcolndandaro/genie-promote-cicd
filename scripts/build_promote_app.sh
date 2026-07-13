#!/usr/bin/env bash
# build_promote_app.sh — assemble the SINGLE front-door app source (SV1).
#
# The single app is a FastAPI backend that serves BOTH the JSON API and the built Svelte SPA from
# the same origin. A Databricks App reads app.yaml from its own source root, so we stage the shared
# Python engine (app/ + genie_reviewer/ + scripts/) + engine_api/ + the BUILT Svelte SPA (as
# static/) into build/promote_app/ with the engine's app.yaml as the root manifest.
# engine_api/main.py resolves the SPA at <app-root>/static.
#
# Run BEFORE `databricks bundle deploy -t prod` (the promote_app resource now lives in the PROD
# target — ADR-0006/A1). `bundle validate` only needs build/promote_app to EXIST, so pr-checks
# stubs the dir instead of running this full build; deploy.yml runs this for real.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="build/promote_app"

# ---- A2: resolve APP_DEV_HOST / APP_DEV_SP_SECRET_SCOPE from the SAME single source of truth
# databricks.yml already declares (the `dev_host` / `dev_sp_secret_scope` bundle variables) —
# instead of baking a hardcoded placeholder here, ask `databricks bundle validate` (which resolves
# --var overrides / target defaults exactly like `bundle deploy` will) what those variables
# currently resolve to, and interpolate the answer into the generated app.yaml. `bundle validate`
# resolves variables from LOCAL bundle files without needing a live/authenticated round-trip, so
# this stays fast and offline-friendly (pr-checks doesn't even reach this script — it stubs the
# build dir — but deploy.yml and any local run do). ADR-0004: env var overrides
# (BUNDLE_DEV_HOST / BUNDLE_DEV_SP_SECRET_SCOPE) still let CI/an operator override without editing
# this file, and a hardcoded fallback keeps this script usable even if `bundle validate` can't run
# (e.g. a bare checkout with no CLI installed yet).
_bundle_var() {  # <var-name> <fallback>
  local var_name="$1" fallback="$2" resolved
  resolved=$(databricks bundle validate -t prod --var "warehouse_id=${DATABRICKS_BUNDLE_WAREHOUSE_ID:-placeholder}" -o json 2>/dev/null \
    | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    v = d.get('variables', {}).get('$var_name', {})
    out = v.get('value') or v.get('default') or ''
    print(out)
except Exception:
    pass
" || true)
  echo "${resolved:-$fallback}"
}

APP_DEV_HOST="${BUNDLE_DEV_HOST:-$(_bundle_var dev_host https://your-dev-workspace.cloud.databricks.com)}"
APP_DEV_SP_SECRET_SCOPE="${BUNDLE_DEV_SP_SECRET_SCOPE:-$(_bundle_var dev_sp_secret_scope genie_promote)}"
# A3/G5 (found live): rehydrate binds the recreated dev Space to a dev SQL warehouse — without this
# env the engine raises at run time ("engine error: rehydrate_space"). Same config-driven pattern.
APP_DEV_WAREHOUSE_ID="${BUNDLE_DEV_WAREHOUSE_ID:-$(_bundle_var dev_warehouse_id your-dev-warehouse-id)}"
echo "resolved APP_DEV_HOST=$APP_DEV_HOST APP_DEV_SP_SECRET_SCOPE=$APP_DEV_SP_SECRET_SCOPE APP_DEV_WAREHOUSE_ID=$APP_DEV_WAREHOUSE_ID"

# 1. Build the Svelte SPA -> web/dist
if [ ! -d web/node_modules ]; then
  (cd web && npm install)
fi
(cd web && npm run build)

# 2. Assemble the app source.
rm -rf "$OUT"
mkdir -p "$OUT"
cp -R app genie_reviewer scripts engine_api "$OUT/"
cp requirements.txt "$OUT/requirements.txt"

# The single app's OWN manifest (distinct from the legacy engine_api/app.yaml): same entrypoint,
# but NO Bearer OBO fallback — it's same-origin, so the proxy always injects the user token and
# the Bearer branch would be pure attack surface.
#
# NOTE: this heredoc stays QUOTED (<<'YAML' — no shell interpolation) because the comments below
# are full of backticks (markdown-style code spans) and literal `$`-looking bundle-var references
# that an UNQUOTED heredoc would try to execute as command substitution / variable expansion. The
# two values that DO need to be config-driven (APP_DEV_HOST / APP_DEV_SP_SECRET_SCOPE, resolved
# above) are written as __APP_DEV_HOST__ / __APP_DEV_SP_SECRET_SCOPE__ placeholders and swapped in
# with `sed` AFTER the heredoc, so the rest of the file's `$`/backtick-heavy prose stays inert.
cat > "$OUT/app.yaml" <<'YAML'
command:
  - python
  - engine_api/run.py
env:
  - name: APP_DOMAIN
    value: "recebiveis"
  # The configured Steward (separation of duties): the distinct approver, surfaced by /whoami so
  # the SPA can enforce "the requester can never approve their own promotion" (SV4).
  - name: APP_STEWARD
    value: "pedro.perdomo@databricks.com"
  # Config-driven Steward/Admin set (LB5): who may see ALL promotions (cross-user governance view).
  # Comma-separated; NO hardcoded binding (ADR-0004) — a new customer sets their own. The Steward
  # (APP_STEWARD) is implicitly included; this adds the platform operator. To grant the view to
  # MULTIPLE stewards without making them full admins, also set APP_STEWARDS (comma-separated) — the
  # server unions APP_ADMINS + APP_STEWARDS + APP_STEWARD.
  - name: APP_ADMINS
    value: "malcoln.dandaro@databricks.com"
  # NOTE: the GitHub icon's target is config-driven (ADR-0004) via APP_REPO_URL, which defaults in the
  # backend (`engine_api/main.py:_repo_url`) to the accelerator's own repo — the SINGLE source of
  # truth. A fork overrides it by setting APP_REPO_URL here; left unset, the default applies.
  # Lakebase is a HARD dependency (ADR-0005): require the durable store at startup, so a bundle that
  # drops the `database` binding (no PGHOST injected) fails fast + loud instead of silently degrading.
  - name: APP_REQUIRE_STORE
    value: "1"
  # Scheduled reconciler cadence (LB6): the in-app periodic task reconciles non-terminal promotions
  # every N seconds so an overnight merge/deploy is recorded even when no browser is open. 0/unset
  # disables it. Config-driven (ADR-0004).
  - name: APP_RECONCILE_INTERVAL_SEC
    value: "300"
  # Per-space promotion slugs (config-driven, ADR-0004): each space promotes on its OWN branch + file
  # keyed by its slug (default: derived from the space id). Pin a friendly/legacy slug here so a space
  # keeps its existing committed file + prod resource — the canonical demo space stays "receivables".
  # A new customer sets their own map (or leaves it empty -> id-derived slugs).
  - name: APP_SPACE_SLUGS
    value: '{"01f16e8322661161a83f7d1f2a1bec14": "receivables"}'
  # Cross-workspace client factory (ADR-0006/A1): the app now runs in PROD and reaches into DEV for
  # the Genie-API ops that must run there. APP_DEV_HOST is the dev workspace URL (config-driven, ADR-
  # 0004 — never hardcoded in code); it and APP_DEV_SP_SECRET_SCOPE are resolved ABOVE from the
  # `dev_host` / `dev_sp_secret_scope` bundle variables (databricks.yml is the single source of
  # truth — see the `_bundle_var` helper at the top of this script), not hardcoded here (A2). The
  # dev-reader/writer SP's OAuth CREDENTIALS themselves are still read at runtime from the secret
  # scope named by APP_DEV_SP_SECRET_SCOPE (keys: dev_sp_client_id, dev_sp_client_secret) — never a
  # bundle var, never in this file. The SP is provisioned by scripts/provision_dev_sp.sh (A2).
  - name: APP_DEV_HOST
    value: "__APP_DEV_HOST__"
  - name: APP_DEV_SP_SECRET_SCOPE
    value: "__APP_DEV_SP_SECRET_SCOPE__"
  - name: APP_DEV_WAREHOUSE_ID
    value: "__APP_DEV_WAREHOUSE_ID__"
YAML
# Swap the two placeholders for the resolved bundle-variable values (see the `_bundle_var` helper
# above) — done with `sed` rather than shell interpolation inside the heredoc so the heredoc's own
# markdown-style prose (backticks, literal `$var` mentions in comments) never gets misread as
# command substitution. `|` is used as the sed delimiter since a workspace URL contains `/`.
sed -i.bak \
  -e "s|__APP_DEV_HOST__|${APP_DEV_HOST}|" \
  -e "s|__APP_DEV_SP_SECRET_SCOPE__|${APP_DEV_SP_SECRET_SCOPE}|" \
  -e "s|__APP_DEV_WAREHOUSE_ID__|${APP_DEV_WAREHOUSE_ID}|" \
  "$OUT/app.yaml"
rm -f "$OUT/app.yaml.bak"  # BSD sed (macOS) requires -i's backup suffix arg; GNU sed treats it the same
# The built SPA the FastAPI app serves (engine_api/main.py -> <app-root>/static).
cp -R web/dist "$OUT/static"

# 3. Hygiene: never ship compiled bytecode (stale .pyc can mask source changes) or build/CI scripts.
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$OUT" -name '*.pyc' -delete
rm -f "$OUT/scripts/build_promote_app.sh" "$OUT/scripts/provision_ci.sh"

echo "assembled single-app source at $OUT (FastAPI + Python engine + Svelte static)"

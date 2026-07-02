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
  # 0004 — never hardcoded in code). The dev-reader/writer SP's OAuth credentials are read at runtime
  # from the secret scope named by APP_DEV_SP_SECRET_SCOPE (keys: dev_sp_client_id,
  # dev_sp_client_secret) — never a bundle var, never in this file. The SP is provisioned in A2; until
  # then these vars are wired but unused (the dev-remote-SP client path isn't exercised live).
  - name: APP_DEV_HOST
    value: "https://your-dev-workspace.cloud.databricks.com"
  - name: APP_DEV_SP_SECRET_SCOPE
    value: "genie_promote"
YAML
# The built SPA the FastAPI app serves (engine_api/main.py -> <app-root>/static).
cp -R web/dist "$OUT/static"

# 3. Hygiene: never ship compiled bytecode (stale .pyc can mask source changes) or build/CI scripts.
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$OUT" -name '*.pyc' -delete
rm -f "$OUT/scripts/build_promote_app.sh" "$OUT/scripts/provision_ci.sh"

echo "assembled single-app source at $OUT (FastAPI + Python engine + Svelte static)"

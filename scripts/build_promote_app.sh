#!/usr/bin/env bash
# build_promote_app.sh — assemble the SINGLE front-door app source (SV1).
#
# The single app is a FastAPI backend that serves BOTH the JSON API and the built Svelte SPA from
# the same origin. A Databricks App reads app.yaml from its own source root, so we stage the shared
# Python engine (app/ + genie_reviewer/ + scripts/) + engine_api/ + the BUILT Svelte SPA (as
# static/) into build/promote_app/ with the engine's app.yaml as the root manifest.
# engine_api/main.py resolves the SPA at <app-root>/static.
#
# Run BEFORE `databricks bundle validate/deploy -t dev` (the promote_app resource points here).
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
  # Lakebase is a HARD dependency (ADR-0005): require the durable store at startup, so a bundle that
  # drops the `database` binding (no PGHOST injected) fails fast + loud instead of silently degrading.
  - name: APP_REQUIRE_STORE
    value: "1"
  # Scheduled reconciler cadence (LB6): the in-app periodic task reconciles non-terminal promotions
  # every N seconds so an overnight merge/deploy is recorded even when no browser is open. 0/unset
  # disables it. Config-driven (ADR-0004).
  - name: APP_RECONCILE_INTERVAL_SEC
    value: "300"
YAML
# The built SPA the FastAPI app serves (engine_api/main.py -> <app-root>/static).
cp -R web/dist "$OUT/static"

# 3. Hygiene: never ship compiled bytecode (stale .pyc can mask source changes) or build/CI scripts.
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$OUT" -name '*.pyc' -delete
rm -f "$OUT/scripts/build_promote_app.sh" "$OUT/scripts/provision_ci.sh"

echo "assembled single-app source at $OUT (FastAPI + Python engine + Svelte static)"

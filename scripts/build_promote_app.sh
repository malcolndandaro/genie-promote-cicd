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
YAML
# The built SPA the FastAPI app serves (engine_api/main.py -> <app-root>/static).
cp -R web/dist "$OUT/static"

# 3. Hygiene: never ship compiled bytecode (stale .pyc can mask source changes) or build/CI scripts.
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$OUT" -name '*.pyc' -delete
rm -f "$OUT/scripts/build_engine_app.sh" "$OUT/scripts/build_promote_app.sh" \
  "$OUT/scripts/provision_ci.sh"

echo "assembled single-app source at $OUT (FastAPI + Python engine + Svelte static)"

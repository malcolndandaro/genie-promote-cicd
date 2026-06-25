#!/usr/bin/env bash
# deploy_dev.sh — one command to deploy the dev target without forgetting the generated dirs.
#
# Two dev resources read gitignored, generated source: the engine API app from build/engine_app
# (scripts/build_engine_app.sh) and (for parity with prod) the rendered build/ artifacts
# (scripts/render.sh). A raw `bundle deploy -t dev` fails if build/engine_app is missing, so this
# wrapper assembles first. Pass extra bundle flags through, e.g. `deploy_dev.sh -p cerc-mlops-dev`.
set -euo pipefail
cd "$(dirname "$0")/.."

bash scripts/build_engine_app.sh
bash scripts/render.sh dev

DATABRICKS_BUNDLE_ENGINE=direct databricks bundle deploy -t dev "$@"
echo "Deployed dev. Start/refresh apps with: databricks bundle run <promotor|engine_api> -t dev $*"

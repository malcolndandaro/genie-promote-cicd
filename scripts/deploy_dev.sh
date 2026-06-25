#!/usr/bin/env bash
# deploy_dev.sh — one command to deploy the dev target without forgetting the generated dirs.
#
# The dev app reads gitignored, generated source: the single app from build/promote_app
# (scripts/build_promote_app.sh) plus (for parity with prod) the rendered build/ artifacts
# (scripts/render.sh). A raw `bundle deploy -t dev` fails if build/promote_app is missing, so this
# wrapper assembles first. Pass extra bundle flags through, e.g. `deploy_dev.sh -p cerc-mlops-dev`.
set -euo pipefail
cd "$(dirname "$0")/.."

bash scripts/build_promote_app.sh  # single front-door app (genie-promote-app): FastAPI + Svelte
bash scripts/render.sh dev

DATABRICKS_BUNDLE_ENGINE=direct databricks bundle deploy -t dev "$@"
echo "Deployed dev. Start/refresh the single app with: databricks bundle run promote_app -t dev $*"

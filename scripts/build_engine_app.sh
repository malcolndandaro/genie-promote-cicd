#!/usr/bin/env bash
# build_engine_app.sh — assemble a self-contained source dir for the engine API app.
#
# A Databricks App reads app.yaml from its OWN source root, and the engine API needs the shared
# engine (app/ + genie_reviewer/ + scripts/) alongside engine_api/ + its own app.yaml. So we stage
# everything into build/engine_app/ with the engine's app.yaml as the root manifest. Mirrors the
# existing build/genie pattern (generated, gitignored, read directly by the resource).
# Run this BEFORE `databricks bundle validate/deploy -t dev` (the engine_api resource points at it).
set -euo pipefail
OUT="build/engine_app"
rm -rf "$OUT"
mkdir -p "$OUT"

# Shared engine (unchanged) + the API layer.
cp -R app genie_reviewer scripts engine_api "$OUT/"

# The engine app's manifest at its source root: command `python engine_api/run.py`.
cp engine_api/app.yaml "$OUT/app.yaml"
cp requirements.txt "$OUT/requirements.txt"

# Never ship compiled bytecode (stale .pyc can mask source changes) or the build/CI scripts.
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$OUT" -name '*.pyc' -delete
rm -f "$OUT/scripts/build_engine_app.sh" "$OUT/scripts/provision_ci.sh"

echo "assembled engine app source at $OUT"

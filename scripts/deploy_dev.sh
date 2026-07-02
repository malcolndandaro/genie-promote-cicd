#!/usr/bin/env bash
# deploy_dev.sh — one command to deploy the dev target.
#
# ADR-0006/A1: the `genie-promote-app` App + its Lakebase instance moved to the PROD target (dev is
# wiped on a ~7-day cycle and can't host durable governance state) — the prod deploy path is the CI
# `deploy.yml` workflow (gated behind PR review + the Steward's Environment approval, per ADR-0002's
# SoD model; it runs `scripts/build_promote_app.sh` before `bundle deploy -t prod`). Dev now only
# carries the `setup` seed job, so this wrapper is just a thin render-then-deploy convenience. Pass
# extra bundle flags through, e.g. `deploy_dev.sh -p cerc-mlops-dev`.
set -euo pipefail
cd "$(dirname "$0")/.."

bash scripts/render.sh dev

DATABRICKS_BUNDLE_ENGINE=direct databricks bundle deploy -t dev "$@"
echo "Deployed dev (setup job only — the app lives in prod, deployed via CI; see deploy.yml)."

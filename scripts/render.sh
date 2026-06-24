#!/usr/bin/env bash
# render.sh <env> — pre-render EVERY promotable artifact (Genie space + AI/BI dashboard)
# from the committed dev source to the target env, and assert each references only
# <env>_<domain> (S4 + S12 / ADR-0003). Run before `bundle validate/deploy -t <env>`.
# Config-driven (ADR-0004): FROM_ENV/DOMAIN overridable via env.
set -euo pipefail
ENV="${1:?usage: render.sh <dev|prod>}"
FROM_ENV="${FROM_ENV:-dev}"
DOMAIN="${DOMAIN:-recebiveis}"

render_one() {  # <src> <out>
  local src="$1" out="$2"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$out")"
  rm -f "$out"   # never deploy a stale build (S4 review)
  python3 scripts/pre_render.py render "$src" --from "$FROM_ENV" --to "$ENV" --domain "$DOMAIN" --out "$out"
  python3 scripts/pre_render.py check "$out" --to "$ENV" --domain "$DOMAIN"
}

# Genie Space (S4) — the same transform also covers the AI/BI dashboard (S12); a dashboard
# has no eval-run, only this deterministic pre-render + allowlist.
render_one "src/genie/receivables.serialized_space.json" "build/genie/receivables.serialized_space.json"
render_one "src/dashboards/recebiveis.lvdash.json" "build/dashboards/recebiveis.lvdash.json"

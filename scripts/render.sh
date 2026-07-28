#!/usr/bin/env bash
# render.sh <env> — pre-render EVERY promotable artifact (per-space Genie space + AI/BI dashboard)
# from the committed dev source to the target env, assert each references only <env>_<domain>
# (S4 + S12 / ADR-0003), AND generate one prod genie_spaces resource per space (per-space promotion).
# Run before `bundle validate/deploy -t <env>`. Config-driven (ADR-0004): FROM_ENV/DOMAIN overridable.
set -euo pipefail
ENV="${1:?usage: render.sh <dev|prod>}"
FROM_ENV="${FROM_ENV:-dev}"
DOMAIN="${DOMAIN:-recebiveis}"

render_one() {  # <src> <out> [<mapping>] [<scan>]
  local src="$1" out="$2" mapping="${3:-}" scan="${4:-all}"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$out")"
  rm -f "$out"   # never deploy a stale build (S4 review)
  python3 scripts/pre_render.py render "$src" --from "$FROM_ENV" --to "$ENV" --domain "$DOMAIN" --out "$out" --scan "$scan"
  # G7: the promotion's declared table de-para (if any), applied AFTER the rebind — the allowlist
  # check below stays UNWIDENED (unlike rehydrate's G6 preview, which widens for the caller's OWN
  # de-para): a mapped target outside <to_env>_<domain> must FAIL here, that's ENV-01 doing its job.
  if [ -n "$mapping" ] && [ -f "$mapping" ]; then
    python3 scripts/pre_render.py apply-mapping "$out" --mapping "$mapping" --from "$FROM_ENV" --to "$ENV" --domain "$DOMAIN"
  fi
  # `scan` selects WHICH text the strict allowlist inspects: `all` (whole document, the Genie
  # default) or `dashboard-sql` (an AI/BI dashboard's datasets[].queryLines only). A dashboard's
  # markdown/text widgets are prose, not a data path, and scanning them produces false
  # foreign-catalog BLOCKERs — a real dev dashboard's markdown link made the 3-part-ref grammar
  # report catalog `en` from `en.wikipedia.org`. See pre_render.dashboard_sql_text.
  python3 scripts/pre_render.py check "$out" --to "$ENV" --domain "$DOMAIN" --scan "$scan"
}

# PER-SPACE Genie spaces: each promotion commits its own src/genie/<slug>.serialized_space.json, so we
# render EACH and emit one prod genie_spaces resource per slug. DABs YAML can't loop, so we generate a
# prod-scoped resource file (build/, gitignored) that databricks.yml includes via `build/*.gen.yml`.
mkdir -p build/genie
GEN="build/resources.gen.yml"
shopt -s nullglob
entries=""
space_count=0
for src in src/genie/*.serialized_space.json; do
  slug="$(basename "$src" .serialized_space.json)"
  space_count=$((space_count + 1))
  render_one "$src" "build/genie/${slug}.serialized_space.json" "src/genie/${slug}.mapping.json"
  # file_path is resolved relative to THIS generated file's dir (build/), so it's ./genie/... not ./build/genie/...
  entries+="        ${slug}:\n"
  entries+="          warehouse_id: \${var.warehouse_id}\n"
  entries+="          file_path: ./genie/${slug}.serialized_space.json\n"
  # Optional per-space title sidecar (committed by the app next to the artifact).
  if [ -f "src/genie/${slug}.title" ]; then
    title="$(tr -d '\n' < "src/genie/${slug}.title" | sed 's/"/\\"/g')"
    entries+="          title: \"${title}\"\n"
    # Copy the title sidecar forward so the deploy resolves the live Space id without guessing.
    cp "src/genie/${slug}.title" "build/genie/${slug}.title"
  fi
  # Required canonical AudienceSpec.
  if [ -f "src/genie/${slug}.audience.json" ]; then
    cp "src/genie/${slug}.audience.json" "build/genie/${slug}.audience.json"
  fi
done
# PER-DASHBOARD AI/BI dashboards: the exact same per-slug promotion shape as the Genie spaces above.
# Previously this was ONE hardcoded filename + resource key + display name, which meant a dashboard
# could be deployed but never PROMOTED through the app (no slug, no sidecars, no Promotion, no audit).
# Now each promotion commits its own src/dashboards/<slug>.lvdash.json and we emit one prod
# `dashboards` resource per slug — so a second dashboard needs no engine change.
#
# Two things differ from the Genie loop, both deliberate:
#   1. the allowlist scan is `dashboard-sql` (see render_one) — prose must not produce false BLOCKERs;
#   2. a non-empty `.title` sidecar is REQUIRED. It becomes `display_name`, which is ALSO the deploy's
#      only id-resolution key (`bundle summary` reports no dashboard id), so a missing title would
#      leave a deployed dashboard unresolvable. Fail closed at render rather than mid-deploy.
mkdir -p build/dashboards
dash_entries=""
dash_count=0
for src in src/dashboards/*.lvdash.json; do
  slug="$(basename "$src" .lvdash.json)"
  dash_count=$((dash_count + 1))
  render_one "$src" "build/dashboards/${slug}.lvdash.json" "src/dashboards/${slug}.mapping.json" "dashboard-sql"
  title_file="src/dashboards/${slug}.title"
  if [ ! -s "$title_file" ]; then
    echo "::error title=render::${slug}: required non-empty .title sidecar is missing (it becomes display_name AND the deploy's id-resolution key)" >&2
    exit 1
  fi
  title="$(tr -d '\n' < "$title_file" | sed 's/"/\\"/g')"
  # file_path is resolved relative to THIS generated file's dir (build/), so ./dashboards/... not
  # ./build/dashboards/...
  dash_entries+="        ${slug}:\n"
  dash_entries+="          display_name: \"${title}\"\n"
  dash_entries+="          warehouse_id: \${var.warehouse_id}\n"
  dash_entries+="          file_path: ./dashboards/${slug}.lvdash.json\n"
  # Copy the sidecars forward so the deploy resolves the live id + reconciles the audience without
  # reaching back into src/ (mirrors the Genie loop exactly).
  cp "$title_file" "build/dashboards/${slug}.title"
  if [ -f "src/dashboards/${slug}.audience.json" ]; then
    cp "src/dashboards/${slug}.audience.json" "build/dashboards/${slug}.audience.json"
  fi
done

# ONE generated file, both collections, BOTH under `targets: prod:`. Prod-scoping the dashboards is a
# fix, not just tidiness: the previous appended block sat at the top level, so a `-t dev` deploy would
# have created prod dashboards in dev. Promoted content is prod-only — dev is human-authored.
{
  echo "# GENERATED by scripts/render.sh — do not edit. One prod resource per promoted artifact"
  echo "# (per-resource promotion): genie_spaces + AI/BI dashboards. Prod-scoped: dev deploys neither"
  echo "# (dev is human-authored)."
  echo "targets:"
  echo "  prod:"
  echo "    resources:"
  if [ -n "$entries" ]; then
    echo "      genie_spaces:"
    printf "%b" "$entries"
  else
    echo "      genie_spaces: {}"
  fi
  if [ -n "$dash_entries" ]; then
    echo "      dashboards:"
    printf "%b" "$dash_entries"
  fi
} > "$GEN"
echo "generated $GEN (${space_count} space(s), ${dash_count} dashboard(s))"

# Setup job (seeds synthetic demo data): also GENERATED (was resources/setup.job.yml, removed in the
# split — its notebook src/setup/seed_recebiveis.py is content, absent from a standalone app checkout).
# Emitted into its own build/*.gen.yml (picked up by the same `build/*.gen.yml` include) only when the
# notebook exists under overlay; standalone it's a comment-only stub. The job spec is byte-identical to
# the old resources/setup.job.yml — INCLUDING no target scoping, so it applies to whichever target is
# deployed (dev AND prod, exactly as before). `../src/setup/...` resolves the same from build/ as it did
# from resources/ (both are one level under the repo root).
SETUP_NB="src/setup/seed_recebiveis.py"
SETUP_GEN="build/setup.gen.yml"
if [ -f "$SETUP_NB" ]; then
  cat > "$SETUP_GEN" << 'YAML'
# GENERATED by scripts/render.sh — do not edit. Setup job (synthetic seed) for the demo domain.
# Un-scoped (applies to all targets), matching the pre-split resources/setup.job.yml exactly.
resources:
  jobs:
    setup:
      name: "[${bundle.target}] recebiveis-setup"
      max_concurrent_runs: 1
      tasks:
        - task_key: seed_recebiveis
          notebook_task:
            notebook_path: ../src/setup/seed_recebiveis.py
            base_parameters:
              catalog: "${var.env}_${var.domain}"
              env: "${var.env}"
YAML
  echo "generated $SETUP_GEN (setup job included)"
else
  echo "# GENERATED by scripts/render.sh — no content overlay, setup job omitted" > "$SETUP_GEN"
  echo "generated $SETUP_GEN (no src/setup — stub only)"
fi

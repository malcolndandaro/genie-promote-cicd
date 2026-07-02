"""CI GRANT-01 gate (pr-checks). Does every declared principal have SELECT on every
data_source table of the pre-rendered, prod serialized_space?

This runs in CI, where the workflow authenticates to the PROD workspace as the CI/CD
service principal (DATABRICKS_HOST + oauth-m2m env) — so it has the prod-catalog
visibility the dev-hosted app lacks, and it gates the PR/merge. Exits non-zero on any
GRANT-01 finding (missing or unverifiable grant). The deterministic GRANT-01 logic lives
in genie_reviewer/grant_check.py (shared with the app + the local verify script).

F2: if a per-space `<slug>.access.json` sidecar is committed next to the rendered space (the
app-declared AccessSpec, mirroring the `.title` sidecar), EVERY principal it declares — across
BOTH the Space-permissions and UC-grants halves — is checked, not just the single legacy
CONSUMER_GROUP. A missing sidecar falls back to the original single-group behavior (no AccessSpec
declared yet == the pre-F2 pipeline, still gated on the base consumer group).

Usage:
    python3 scripts/check_grants.py [rendered_space.json] [consumer_group] [access_sidecar.json]
Local test against a profile:
    DATABRICKS_CONFIG_PROFILE=cerc-mlops-prod python3 scripts/check_grants.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import access_spec  # noqa: E402
import grant_check  # noqa: E402

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import SecurableType

DEFAULT_PATH = "build/genie/receivables.serialized_space.json"


def _declared_principals(sidecar_path: str, consumer_group: str) -> list[str]:
    """All principals GRANT-01 must verify: the base consumer group PLUS every principal declared
    in the AccessSpec sidecar (if one was committed for this promotion). Union, not replace — the
    base group check is never weakened by an AccessSpec being present."""
    principals = [consumer_group]
    if sidecar_path and os.path.exists(sidecar_path):
        with open(sidecar_path, encoding="utf-8") as fh:
            spec = access_spec.AccessSpec.from_dict(json.load(fh))
        principals += list(spec.all_principals())
    return principals


def _effective_grants(w: WorkspaceClient, full_name: str) -> list[dict]:
    """Effective UC privileges on a table (includes schema/catalog-inherited grants).
    Mirrors app/app_logic.py::_effective_grants; SDK 0.111 wants the string 'TABLE'."""
    resp = w.grants.get_effective(securable_type=SecurableType.TABLE.value, full_name=full_name)
    out = []
    for pa in (resp.privilege_assignments or []):
        privs = [getattr(p.privilege, "value", p.privilege) for p in (pa.privileges or [])]
        out.append({"principal": pa.principal, "privileges": [x for x in privs if x]})
    return out


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    consumer_group = (sys.argv[2] if len(sys.argv) > 2 else None) \
        or os.environ.get("CONSUMER_GROUP", "account users")
    sidecar_path = sys.argv[3] if len(sys.argv) > 3 else None
    if not os.path.exists(path):
        print(f"GRANT-01: nada a checar — {path} não existe (rode scripts/render.sh primeiro).")
        return 0
    with open(path, encoding="utf-8") as fh:
        space = json.load(fh)

    principals = _declared_principals(sidecar_path, consumer_group)
    w = WorkspaceClient()  # CI: prod SP via env; local: DATABRICKS_CONFIG_PROFILE
    findings = grant_check.check_grants(space, principals, lambda fq: _effective_grants(w, fq))

    if findings:
        print(f"🔴 GRANT-01 — promoção bloqueada ({len(findings)} achado(s)) "
              f"para os principais declarados {principals}:")
        for f in findings:
            print(f"  - {f.get('table')} / {f.get('principal', consumer_group)}: {f.get('message')}")
        return 1
    print(f"✅ GRANT-01 — todos os principais declarados {principals} têm SELECT em todas as tabelas do espaço.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

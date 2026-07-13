"""Grant the promote-app's SP CAN_READ on a just-deployed prod Genie Space (idempotent).

WHY (found live, 2026-07-13): the prod-hosted app lists/exports prod Spaces as ITS OWN SP
(`list_prod_spaces`, the rehydrate export) — Genie has no workspace-level listing for
non-admins, so a Space the app SP holds no ACL on simply doesn't exist for the app: the
Acesso/Rehidratar pickers render "Nenhum resultado". Granting per-Space by hand does not
scale (the stakeholder's exact complaint), so the PIPELINE does it: this runs in deploy.yml
right after `apply_access.py`, AS the prod CI SP, for EVERY deployed space — the same
governed identity/step that created the Space makes it visible to the app. Config-driven
(ADR-0004): the app name comes from APP_NAME (default genie-promote-app); the SP id is
resolved live from the app itself, never hardcoded.

Usage: python3 scripts/grant_app_sp_read.py "<space title>"
Exits non-zero if the Space or the app can't be resolved (never warn-and-skip).
"""
from __future__ import annotations

import os
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import iam

# Reuse the exact same resolve-by-title logic (and its fail-loud contract) as apply_access.
sys.path.insert(0, os.path.dirname(__file__))
from apply_access import resolve_space_id  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: grant_app_sp_read.py '<space title>'", file=sys.stderr)
        return 2
    title = sys.argv[1].strip()
    app_name = os.environ.get("APP_NAME", "genie-promote-app")

    w = WorkspaceClient()  # CI: prod SP via env; local: DATABRICKS_CONFIG_PROFILE
    app = w.apps.get(app_name)
    app_sp = app.service_principal_client_id
    if not app_sp:
        print(f"grant_app_sp_read: app '{app_name}' has no service_principal_client_id", file=sys.stderr)
        return 1

    space_id = resolve_space_id(w, title)
    # permissions.update is ADDITIVE (never strips other principals' grants) and re-running
    # with the same level is a no-op — safe on every deploy.
    w.permissions.update(
        request_object_type="genie",
        request_object_id=space_id,
        access_control_list=[iam.AccessControlRequest(
            service_principal_name=app_sp,
            permission_level=iam.PermissionLevel.CAN_READ,
        )],
    )
    print(f"grant_app_sp_read: CAN_READ -> app SP ({app_sp}) on space '{title}' (id={space_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

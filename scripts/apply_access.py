#!/usr/bin/env python3
"""Temporary compatibility entrypoint for the old content workflow.

Despite the historical filename, this script cannot grant Unity Catalog privileges. It reads a
canonical AudienceSpec (or safely translates legacy CAN_RUN Space entries), resolves the live Space
and reconciles only Genie audience ACLs. It is deleted when the content workflow switches in
ADR-0009 Phase 2.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
sys.path.insert(0, os.path.dirname(__file__))
import audience_spec  # noqa: E402
import reconcile_audience  # noqa: E402

from databricks.sdk import WorkspaceClient  # noqa: E402


def resolve_space_id(w: WorkspaceClient, title: str) -> str:
    """Resolve exactly one live Space by title; never guess on absent/duplicate matches."""
    if not title:
        raise ValueError("apply_access: no title provided — cannot resolve the deployed Space id")
    matches = [space.space_id for space in (w.genie.list_spaces().spaces or [])
               if (space.title or "") == title]
    if not matches:
        raise ValueError(f"apply_access: no deployed Genie Space found with title {title!r}")
    if len(matches) > 1:
        raise ValueError(
            f"apply_access: {len(matches)} deployed Genie Spaces share title {title!r} "
            f"(ids={matches}) — refusing to guess"
        )
    return matches[0]


def _warning(message: str) -> None:
    from check_grants import _gh_escape
    print(f"::warning title=AudienceSpec legacy::{_gh_escape(message)}")


def load_audience(path: str) -> audience_spec.AudienceSpec:
    with open(path, encoding="utf-8") as handle:
        return audience_spec.parse_sidecar(json.load(handle), warn=_warning)


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: apply_access.py <rendered_space.json> <audience_or_legacy.json> <space_title>",
              file=sys.stderr)
        return 2
    _rendered_path, sidecar_path, title = sys.argv[1], sys.argv[2], sys.argv[3]
    if not os.path.exists(sidecar_path):
        print(f"AudienceSpec: no sidecar at {sidecar_path}; skipping compatibility step")
        return 0
    desired = load_audience(sidecar_path)
    w = WorkspaceClient()
    space_id = resolve_space_id(w, title)
    result = reconcile_audience.reconcile(w, space_id, desired)
    print(f"AudienceSpec reconciled on {title!r}: {result['principals']}")
    return 0


def _run() -> int:
    from check_grants import _gh_escape
    try:
        return main()
    except BaseException as exc:  # noqa: BLE001 — annotate then preserve the failure
        print(f"::error title=apply-access::{_gh_escape(f'{type(exc).__name__}: {exc}')}")
        raise


if __name__ == "__main__":
    raise SystemExit(_run())

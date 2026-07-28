"""workspace_resource — the per-kind Databricks API adapter (the SDK half of the kind seam).

`resource_kind` holds the per-kind FACTS (paths, object types, levels). This module holds the per-kind
CALLS, so nothing else in the codebase branches on kind to reach the workspace:

    list_resources(client, kind)                 -> [{"resource_id", "title"}]
    get_serialized(client, kind, resource_id)    -> the definition as a dict
    resolve_by_title(client, kind, title)        -> the live id, or raise (never guess)
    create(client, kind, ...) / update(...)      -> the rehydrate write half

Genie and AI/BI dashboards are deliberately close in shape — both expose a JSON definition as a
STRING on a get (`serialized_space` / `serialized_dashboard`), and neither returns it on a list — so
these functions are thin. Keeping them here rather than inline means adding a third kind is a registry
entry plus one adapter, not a sweep through `app_logic` / `rehydrate` / `deploy_attempt`.

Every function takes an injected ``client`` and never builds one, so the whole module is unit-testable
with a `SimpleNamespace` fake and makes no network call of its own — the same convention
`app_logic` already follows.
"""
from __future__ import annotations

import json
import time
from typing import Callable

import resource_kind as rk

# Bounded post-deploy propagation retry for title-based id resolution. A freshly deployed resource
# can take a moment to appear in a list call, so resolution retries rather than failing the deploy on
# a race — the same 30 x 2s budget the Genie path has always used.
_RESOLVE_MAX_ATTEMPTS = 30
_RESOLVE_RETRY_SECONDS = 2.0


def _as_dict(serialized) -> dict:
    """Normalize a `serialized_*` payload (a JSON string in practice) to a dict."""
    if isinstance(serialized, str):
        return json.loads(serialized) if serialized.strip() else {}
    return serialized or {}


def list_resources(client, kind: rk.ResourceKind) -> "list[dict]":
    """Every resource of ``kind`` the ``client``'s identity can see: ``[{resource_id, title}]``.

    NOTE this is the RAW platform view — it is NOT an access boundary. `app_logic` filters it per
    user through `authz.assert_can_access`; see that module for why the standing dev service
    principal's broad reach must never be returned to a caller unfiltered.

    Trashed dashboards are excluded: `lakeview.list` reports them with a non-ACTIVE
    `lifecycle_state`, and a trashed dashboard is not promotable.
    """
    if kind.kind == rk.GENIE_SPACE:
        response = client.genie.list_spaces()
        return [{"resource_id": s.space_id, "title": s.title or "(sem título)"}
                for s in (response.spaces or [])]
    out = []
    for dashboard in client.lakeview.list():
        state = getattr(dashboard, "lifecycle_state", None)
        # The SDK gives an enum; compare on its value so a plain string works too.
        if str(getattr(state, "value", state) or "ACTIVE") != "ACTIVE":
            continue
        out.append({"resource_id": dashboard.dashboard_id,
                    "title": dashboard.display_name or "(sem título)"})
    return out


def get_serialized(client, kind: rk.ResourceKind, resource_id: str) -> dict:
    """One resource's full definition as a dict (`serialized_space` / `serialized_dashboard`).

    Neither kind returns the definition on a LIST call — only on a get — so this is always a
    per-resource round trip.
    """
    if kind.kind == rk.GENIE_SPACE:
        space = client.genie.get_space(resource_id, include_serialized_space=True)
        return _as_dict(space.serialized_space)
    dashboard = client.lakeview.get(resource_id)
    return _as_dict(dashboard.serialized_dashboard)


def get_title(client, kind: rk.ResourceKind, resource_id: str) -> "str | None":
    """The resource's current display name in its own workspace (pre-fills the editable prod name)."""
    if kind.kind == rk.GENIE_SPACE:
        return getattr(client.genie.get_space(resource_id, include_serialized_space=True), "title", None)
    return getattr(client.lakeview.get(resource_id), "display_name", None)


def resolve_by_title(
    client,
    kind: rk.ResourceKind,
    title: str,
    *,
    max_attempts: int = _RESOLVE_MAX_ATTEMPTS,
    retry_seconds: float = _RESOLVE_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Resolve ONE live resource id by title, allowing bounded post-deploy propagation.

    Title is the resolution key because `bundle summary` reports neither a Genie space id nor a
    dashboard id, so the deploy has no other way to learn what it just created.

    REFUSES TO GUESS: two live resources sharing a title raise immediately (retrying wouldn't
    disambiguate, and picking one could reconcile ACLs onto the wrong object). A title that never
    appears raises after the full budget. Both are `ValueError` — a deploy must fail closed here, not
    proceed against an unresolved target.
    """
    if not title:
        raise ValueError(f"no title provided; cannot resolve the deployed {kind.label_pt}")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(max_attempts):
        matches = [r["resource_id"] for r in list_resources(client, kind) if r["title"] == title]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} deployed {kind.label_pt} share title {title!r} "
                f"(ids={matches}); refusing to guess")
        if attempt + 1 < max_attempts:
            sleep(retry_seconds)
    raise ValueError(f"no deployed {kind.label_pt} found with title {title!r}")


def create(client, kind: rk.ResourceKind, *, serialized: dict, title: str | None,
           warehouse_id: str, parent_path: str | None = None):
    """Create a NEW resource from a serialized definition (the rehydrate `create` mode).

    Returns the created object's id. For a dashboard this creates a DRAFT; `publish` is a separate
    step the deploy path owns (a rehydrated dev dashboard needs no publishing to be authored on).
    """
    payload = json.dumps(serialized, ensure_ascii=False)
    if kind.kind == rk.GENIE_SPACE:
        space = client.genie.create_space(
            warehouse_id, serialized_space=payload, title=title, parent_path=parent_path)
        return space.space_id
    from databricks.sdk.service import dashboards as dashboards_svc

    dashboard = client.lakeview.create(dashboards_svc.Dashboard(
        display_name=title, warehouse_id=warehouse_id,
        serialized_dashboard=payload, parent_path=parent_path))
    return dashboard.dashboard_id


def update(client, kind: rk.ResourceKind, resource_id: str, *, serialized: dict,
           title: str | None, warehouse_id: str):
    """Overwrite an EXISTING resource (the rehydrate `overwrite` mode).

    Both APIs are PATCH-shaped: pass every field being reset. ``title=None`` means "keep the existing
    display name" for both kinds.
    """
    payload = json.dumps(serialized, ensure_ascii=False)
    if kind.kind == rk.GENIE_SPACE:
        space = client.genie.update_space(
            resource_id, serialized_space=payload, warehouse_id=warehouse_id, title=title)
        return space.space_id
    from databricks.sdk.service import dashboards as dashboards_svc

    dashboard = client.lakeview.update(resource_id, dashboards_svc.Dashboard(
        display_name=title, warehouse_id=warehouse_id, serialized_dashboard=payload))
    return dashboard.dashboard_id


def publish(client, kind: rk.ResourceKind, resource_id: str, *, warehouse_id: str) -> None:
    """Publish a dashboard so consumers can open it. A no-op for Genie (a Space has no draft state).

    ``embed_credentials=False`` is deliberate and load-bearing: the published dashboard runs queries
    as the VIEWER, so a viewer still needs their own Unity Catalog SELECT and warehouse access.
    Publishing with embedded credentials would make the promotion pipeline a data-access mechanism —
    exactly what ADR-0009 retired when it removed UC grants from this accelerator's remit.
    """
    if kind.kind == rk.GENIE_SPACE:
        return
    client.lakeview.publish(resource_id, embed_credentials=False, warehouse_id=warehouse_id)

"""rehydrate — F1/A3: pull an already-promoted PROD Genie Space back into DEV, no git PR.

Reuses the promotion machinery in reverse (SP1's create/import primitive + `pre_render`'s
already-bidirectional `rebind`/`find_violations`), gated at BOTH blast sites by A2's single
`authz.assert_can_access` guard on the caller's platform-VERIFIED identity:

  1. **source read (prod)** — the caller must be able to access the prod Space being exported.
  2. **overwrite target (dev)** — when overwriting an existing dev Space, the caller must be able
     to access THAT Space too, so a caller can never clobber someone else's dev Space via the
     dev-reader/writer SP's broad reach.

Flow: `get_space(prod, include_serialized_space=True)` -> `pre_render.rebind(prod_->dev_)` ->
`find_violations(..., to_env="dev")` (reverse allowlist: a stray `prod_`/foreign catalog left in
the rebound payload is a bug, never silently created in dev) -> `create_space` (new dev Space) or
`update_space` (overwrite, PATCH — SP1: pass every field being reset) via the dev-reader/writer SP
(`app_logic._client(scope="dev-sp")`).

`benchmarks.questions` need no special handling — SP1 proved they round-trip losslessly through
`create_space`/`update_space` as part of the same `serialized_space` payload.

Precondition disambiguation (SP2): a caller denied by `assert_can_access` gets a normal
`AccessDenied` (403-shaped) — the guard is working as designed. A dev-SP that can't be reached /
isn't granted / can't resolve `APP_DEV_HOST` raises `DevEnvironmentNotBootstrapped` instead, a
DISTINCT exception carrying an actionable "run provision_dev_sp.sh" message — collapsing the two
into one error class would recreate the exact "fails confusingly" bug SP2 exists to prevent.

Audit (non-repudiation): every rehydrate appends one `audit_events` row (via `PromotionStore`)
recording the ACTING identity's `user_name` (the live-checked, platform-verified caller — never a
display header) AND the fact that the dev-reader/writer SP holds a broad standing grant reaching
every dev Space by platform necessity — the human fact ("who did it") and the platform fact ("what
made it technically possible") are recorded distinctly so an incident review can tell "the guard
worked" from "the guard was bypassed" (PRD, Epic A / A2 story 8).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.errors import DatabricksError

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("genie_reviewer", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import authz  # noqa: E402  (A2 — verified identity + the single assert_can_access guard)
import pre_render  # noqa: E402  (already-bidirectional rebind/find_violations, S4/ADR-0003)

DOMAIN = os.environ.get("APP_DOMAIN", "recebiveis")
# The dev warehouse a rehydrated Space is bound to — a create/update param, NOT part of the
# serialized_space payload (SP1: "warehouse binding is a separate parameter"). Config-driven
# (ADR-0004): a second customer sets this env var, never a hardcoded id.
APP_DEV_WAREHOUSE_ID = os.environ.get("APP_DEV_WAREHOUSE_ID")
# Optional explicit dev folder for newly-created rehydrated Spaces (SP1 "Known gaps": pin a target
# rather than rely on default placement). None keeps the SDK's default behavior.
APP_DEV_PARENT_PATH = os.environ.get("APP_DEV_PARENT_PATH") or None


class DevEnvironmentNotBootstrapped(RuntimeError):
    """The dev-reader/writer SP could not be reached/used (unreachable host, ungranted, dev mid-
    wipe) — DISTINCT from `authz.AccessDenied` (SP2's disambiguation). The caller should surface an
    actionable "dev environment needs re-bootstrap (run provision_dev_sp.sh)" message, never a
    generic 403 — a genuinely-denied user must never see a bootstrap prompt, and a mis-provisioned
    dev SP must never look like "you personally lack access"."""


@dataclass(frozen=True)
class RehydrateResult:
    space_id: str          # the resulting dev Space id (new on create, unchanged on overwrite)
    mode: str               # "create" | "overwrite"
    title: Optional[str]
    warehouse_id: Optional[str]


def _no_dev_warehouse() -> None:
    if not APP_DEV_WAREHOUSE_ID:
        raise RuntimeError(
            "APP_DEV_WAREHOUSE_ID is not configured — rehydrate needs a dev SQL warehouse id "
            "(ADR-0004: config-driven, never hardcoded).")


def _rebind_prod_to_dev(prod_serialized: dict, domain: str) -> tuple[dict, list[str]]:
    """Rebind a prod-shaped serialized_space to dev_, then run the REVERSE allowlist
    (`to_env="dev"`) — catches a stray `prod_`/foreign catalog reference left after the rebind
    (e.g. a rule the forward rebind didn't anticipate) before it's ever written to dev."""
    raw = json.dumps(prod_serialized, ensure_ascii=False)
    rebound_raw = pre_render.rebind(raw, "prod", "dev", domain)
    violations = pre_render.find_violations(rebound_raw, "dev", domain)
    return json.loads(rebound_raw), violations


def _dev_transport(client: Optional[WorkspaceClient]) -> WorkspaceClient:
    """Resolve the dev-reader/writer SP transport, translating an unreachable/unconfigured SP into
    the actionable `DevEnvironmentNotBootstrapped` (SP2 disambiguation) rather than letting a bare
    `RuntimeError`/`DatabricksError` leak up looking like an access denial."""
    if client is not None:
        return client
    import app_logic  # local import: avoids a circular import at module load time

    try:
        return app_logic._client(scope="dev-sp")
    except RuntimeError as e:  # APP_DEV_HOST not configured (app_logic._client's own fail-loud)
        raise DevEnvironmentNotBootstrapped(
            "dev environment needs re-bootstrap (run provision_dev_sp.sh): "
            f"{e}") from e


def _audit(store, promotion_id: Optional[str], identity: authz.VerifiedIdentity, *,
           mode: str, source_space_id: str, dev_space_id: str, dev_warehouse_id: Optional[str]) -> None:
    """Append the non-repudiation audit row (PRD Epic A story 15 / A2 story 8): the live-checked
    ACTING identity, distinctly from the fact that the standing dev-reader/writer SP holds a broad
    grant reaching every dev Space by platform necessity (the compensating control this guard IS).
    Uses the `rehydrated` event type (RECURRING — reseeding dev again after another wipe is
    legitimate, not a duplicate milestone). A missing store (no Lakebase bound — local/offline) is
    a no-op, matching every other optional-store call site in this codebase (e.g.
    `engine_api.promote`)."""
    if store is None or promotion_id is None:
        return
    store.append_audit_event(
        promotion_id, "rehydrated",
        actor_app_email=identity.user_name,
        detail={
            "action": "rehydrate", "mode": mode,
            "source_prod_space_id": source_space_id, "dev_space_id": dev_space_id,
            "dev_warehouse_id": dev_warehouse_id,
            "acting_identity": identity.user_name,
            "sp_broad_grant": (
                "dev-reader/writer service principal (scope=dev-sp) can reach every dev Space by "
                "platform necessity (Genie has no per-Space delegation); this action was gated by "
                "assert_can_access on the acting identity above, not by the SP's own reach"),
        })


def rehydrate_space(
    *,
    source_prod_space_id: str,
    identity: authz.VerifiedIdentity,
    mode: str,
    dev_space_id: Optional[str] = None,
    title: Optional[str] = None,
    domain: str = DOMAIN,
    prod_client: Optional[WorkspaceClient] = None,
    dev_client: Optional[WorkspaceClient] = None,
    dev_warehouse_id: Optional[str] = None,
    store=None,
    promotion_id: Optional[str] = None,
) -> RehydrateResult:
    """Export a PROMOTED prod Space and (re-)create it in dev, rebound `prod_`->`dev_`.

    ``mode`` is ``"create"`` (mint a brand-new dev Space) or ``"overwrite"`` (replace an existing
    dev Space's content in place) — see SP1 for why these are two distinct SDK calls
    (``create_space`` is not identity-idempotent; ``update_space`` is PATCH).

    Guarded at BOTH blast sites (A3 acceptance criteria):
      - the caller must ``assert_can_access`` the SOURCE prod Space before it is ever exported;
      - in ``overwrite`` mode, the caller must ALSO ``assert_can_access`` the TARGET dev Space
        before it is touched — so a caller can't clobber a dev Space they can't reach even though
        the standing dev SP itself could reach it.

    ``prod_client``/``dev_client`` are injectable transports (tests / explicit callers); when
    omitted, prod resolves via ``app_logic._client()`` (prod-local: profile/SP) and dev via the
    dev-reader/writer SP (``scope="dev-sp"``), translating a mis-provisioned/unreachable dev SP
    into ``DevEnvironmentNotBootstrapped`` rather than a confusing access-denied.
    """
    if mode not in ("create", "overwrite"):
        raise ValueError(f"mode must be 'create' or 'overwrite', got {mode!r}")
    if mode == "overwrite" and not dev_space_id:
        raise ValueError("overwrite mode requires dev_space_id")
    wh = dev_warehouse_id or APP_DEV_WAREHOUSE_ID
    if not wh:
        _no_dev_warehouse()

    import app_logic  # local import: avoids a circular import at module load time

    # 1) SOURCE guard (prod) — deny before ever exporting. `prod_client` is ALSO the transport for
    #    the ACL check itself (mirrors authz.assert_can_access's "transport only" contract).
    prod = prod_client if prod_client is not None else app_logic._client()
    authz.assert_can_access(identity, source_prod_space_id, transport=prod)
    space = prod.genie.get_space(source_prod_space_id, include_serialized_space=True)
    ss = space.serialized_space
    prod_serialized = json.loads(ss) if isinstance(ss, str) else (ss or {})

    # 2) Rebind prod_->dev_ + REVERSE allowlist (a stray prod_/foreign catalog must never reach dev).
    rebound, violations = _rebind_prod_to_dev(prod_serialized, domain)
    if violations:
        raise ValueError(
            f"rehydrate refused: rebound payload still references non-dev_{domain} catalogs: "
            f"{violations}")

    # 3) TARGET guard (dev, overwrite only) + the dev-sp transport (SP2: unreachable/ungranted SP
    #    raises DevEnvironmentNotBootstrapped, DISTINCT from a genuine access denial).
    dev = _dev_transport(dev_client)
    if mode == "overwrite":
        authz.assert_can_access(identity, dev_space_id, transport=dev)
        # SP1: update_space is PATCH — pass every field we want reset explicitly (title/warehouse),
        # never rely on "serialized_space alone resets the whole resource".
        dev.genie.update_space(
            dev_space_id, serialized_space=json.dumps(rebound, ensure_ascii=False),
            warehouse_id=wh, title=title)
        result_id = dev_space_id
    else:
        created = dev.genie.create_space(
            wh, json.dumps(rebound, ensure_ascii=False),
            title=title, parent_path=APP_DEV_PARENT_PATH)
        result_id = created.space_id

    _audit(store, promotion_id, identity, mode=mode, source_space_id=source_prod_space_id,
           dev_space_id=result_id, dev_warehouse_id=wh)

    return RehydrateResult(space_id=result_id, mode=mode, title=title, warehouse_id=wh)

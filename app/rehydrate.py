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

Audit (non-repudiation): every rehydrate appends one row recording the ACTING identity's
`user_name` (the live-checked, platform-verified caller — never a display header) AND the fact
that the dev-reader/writer SP holds a broad standing grant reaching every dev Space by platform
necessity — the human fact ("who did it") and the platform fact ("what made it technically
possible") are recorded distinctly so an incident review can tell "the guard worked" from "the
guard was bypassed" (PRD, Epic A / A2 story 8). TWO audit paths (stakeholder decision — rehydrate
must work for ANY prod Space the caller can access, not only ones this app promoted; the prod
store starts EMPTY per ADR-0006, so most prod Spaces have no Promotion row at all):
  - a linkable source Promotion exists (`promotion_id` given/resolved) -> the richer
    `promotion_store.audit_events` "rehydrated" row, next to that Promotion's own history;
  - no linkable Promotion -> a STANDALONE `promotion_store.rehydrate_events` row instead (no FK,
    same acting-identity/SP-broad-grant facts) — see `promotion_store.RehydrateEvent`.

G6 (table de-para + editable dev title): `rehydrate_space` accepts an optional `table_mapping`
(source prod ref -> desired dev ref), applied AFTER the standard prod_->dev_ rebind via
`pre_render.apply_table_mapping`, then re-checked with a WIDENED allowlist (the plain
`dev_<domain>` target PLUS every catalog the caller explicitly chose) — a mapped target that
resolves back to the prod catalog is refused outright (never silently written), and anything else
outside that widened allowlist still fails the rehydrate exactly like an un-mapped leak would.
`preview_rehydrate` is the read-only counterpart the UI calls BEFORE this: it exports the same
source Space, extracts every 3-part ref via `pre_render.find_refs`, and returns each ref's plain
default target (+ best-effort dev-side existing-table suggestions) so the UI can render the de-para
BEFORE the user commits to a mapping at all. The existing `title` param already IS the "editable
dev Space name" — `create_space`/`update_space` both already special-case `title=None` as "use
the embedded/existing title unchanged" (see the SDK's own `body["title"] = title if not None`
PATCH semantics), so no new title parameter was needed; only the UI (G6) had to stop synthesizing
it silently and start showing/letting the caller edit it.
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


def _apply_table_mapping(rebound: dict, table_mapping: dict[str, str], *, domain: str) -> dict:
    """G6: apply the caller's table de-para (source prod ref -> desired dev ref) on top of the
    already prod_->dev_ rebound payload, then re-check the allowlist WIDENED to the catalogs the
    caller explicitly chose. HARD-FAILS (`ValueError`, the caller's problem — surfaced as a 400 by
    the API layer) when:
      - a mapped target isn't a well-formed catalog.schema.table ref;
      - a mapped target's catalog is the PROD catalog itself — a de-para is only ever meant to
        redirect WITHIN dev (e.g. a differently-named dev schema/table); pointing it back at prod
        would defeat the exact anti-leak invariant rehydrate exists to enforce, so this is refused
        even though widening the allowlist to "every catalog the caller chose" would otherwise let
        it through;
      - anything ELSE outside the widened allowlist remains after the mapping is applied (the same
        reverse-allowlist protection `_rebind_prod_to_dev` already gives the un-mapped case).
    """
    prod_catalog = f"prod_{domain}"
    dev_catalog = f"dev_{domain}"
    mapped_catalogs: set[str] = set()
    for source_ref, target_ref in table_mapping.items():
        parts = target_ref.split(".")
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"rehydrate refused: de-para inválido para {source_ref!r} — o alvo deve ser "
                f"catalog.schema.table, recebido {target_ref!r}")
        if parts[0].lower() == prod_catalog.lower():
            raise ValueError(
                f"rehydrate refused: o de-para para {source_ref!r} aponta de volta para o catálogo "
                f"de produção ({prod_catalog}) — não é permitido redirecionar uma tabela de dev "
                f"para prod")
        mapped_catalogs.add(parts[0])

    raw = pre_render.apply_table_mapping(
        json.dumps(rebound, ensure_ascii=False), table_mapping, from_env="prod", to_env="dev",
        domain=domain)
    violations = pre_render.find_violations(raw, "dev", domain, extra_allowed_catalogs=mapped_catalogs)
    if violations:
        raise ValueError(
            f"rehydrate refused: payload com de-para ainda referencia catálogos fora do allowlist "
            f"({dev_catalog} + de-para): {violations}")
    return json.loads(raw)


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
           mode: str, source_space_id: str, resource_title: Optional[str], dev_space_id: str,
           dev_warehouse_id: Optional[str], new_title: Optional[str] = None,
           table_mapping: Optional[dict[str, str]] = None) -> None:
    """Append the non-repudiation audit row (PRD Epic A story 15 / A2 story 8): the live-checked
    ACTING identity, distinctly from the fact that the standing dev-reader/writer SP holds a broad
    grant reaching every dev Space by platform necessity (the compensating control this guard IS).
    A missing store (no Lakebase bound — local/offline) is a no-op, matching every other
    optional-store call site in this codebase (e.g. `engine_api.promote`).

    Two paths (see the module docstring): a linkable `promotion_id` gets the richer
    `audit_events` "rehydrated" row (RECURRING — reseeding dev again after another wipe is
    legitimate, not a duplicate milestone); no `promotion_id` gets a STANDALONE
    `rehydrate_events` row instead — same facts, no FK to a Promotion that doesn't exist.

    `new_title`/`table_mapping` (G6) are recorded as-given (both nullable/empty on a plain rehydrate
    with no overrides) so an audit review can see not just THAT a rehydrate happened but whether the
    caller customized the dev title or any table target away from the plain defaults."""
    if store is None:
        return
    detail = {
        "action": "rehydrate", "mode": mode,
        "source_prod_space_id": source_space_id, "dev_space_id": dev_space_id,
        "dev_warehouse_id": dev_warehouse_id,
        "acting_identity": identity.user_name,
        "new_title": new_title,
        "table_mapping": table_mapping or {},
        "sp_broad_grant": (
            "dev-reader/writer service principal (scope=dev-sp) can reach every dev Space by "
            "platform necessity (Genie has no per-Space delegation); this action was gated by "
            "assert_can_access on the acting identity above, not by the SP's own reach"),
    }
    if promotion_id is not None:
        store.append_audit_event(
            promotion_id, "rehydrated", actor_app_email=identity.user_name, detail=detail)
    else:
        store.append_rehydrate_event(
            resource_id=source_space_id, resource_title=resource_title,
            actor_email=identity.user_name, mode=mode, dev_space_id=dev_space_id, detail=detail)


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
    table_mapping: Optional[dict[str, str]] = None,
) -> RehydrateResult:
    """Export a prod Space the caller can access and (re-)create it in dev, rebound
    `prod_`->`dev_` — the source need NOT have been promoted through this app (stakeholder
    decision: the prod store starts empty per ADR-0006, so most prod Spaces have no Promotion row
    at all; see the module docstring for the two resulting audit paths).

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

    ``table_mapping`` (G6, optional): source prod ref -> desired dev ref overrides, applied AFTER
    the standard rebind (see ``_apply_table_mapping``) — raises ``ValueError`` (the caller's
    problem, surfaced as a 400 by the API layer) on a malformed target or one that resolves back to
    the prod catalog, or if the widened allowlist still catches a leak afterward.
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

    # 2b) G6: the caller's table de-para, if any — re-checked with a WIDENED allowlist (raises on a
    #     malformed/prod-pointing override or any remaining leak; see _apply_table_mapping).
    if table_mapping:
        rebound = _apply_table_mapping(rebound, table_mapping, domain=domain)

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

    # getattr: real GenieSpace responses always carry .title, but some test-fake transports don't —
    # resource_title is nullable in the standalone rehydrate_events row anyway (see RehydrateEvent).
    _audit(store, promotion_id, identity, mode=mode, source_space_id=source_prod_space_id,
           resource_title=getattr(space, "title", None), dev_space_id=result_id, dev_warehouse_id=wh,
           new_title=title, table_mapping=table_mapping)

    return RehydrateResult(space_id=result_id, mode=mode, title=title, warehouse_id=wh)


def _dev_table_suggestions(schemas: "set[tuple[str, str]]",
                            dev_client: Optional[WorkspaceClient]) -> dict:
    """G6: best-effort — list existing tables in each (catalog, schema) via the dev-reader/writer
    SP, for the preview's per-row suggestion list. ANY failure (dev SP unreachable/unprovisioned, a
    schema that doesn't exist yet in dev, a missing grant) degrades to an EMPTY list for that schema
    rather than raising — a suggestion is a nicety the UI's free-text override never depends on, not
    a hard dependency of the preview itself."""
    out: dict = {}
    if not schemas:
        return out
    try:
        dev = dev_client if dev_client is not None else _dev_transport(None)
    except Exception:  # noqa: BLE001 — ANY failure here (unreachable/unprovisioned dev SP, etc.)
        return {s: [] for s in schemas}
    for catalog, schema in schemas:
        try:
            out[(catalog, schema)] = sorted(
                f"{catalog}.{schema}.{t.name}" for t in dev.tables.list(catalog, schema) if t.name)
        except Exception:  # noqa: BLE001 — a schema not existing yet in dev is not fatal, no suggestions
            out[(catalog, schema)] = []
    return out


def preview_rehydrate(source_prod_space_id: str, *, identity: authz.VerifiedIdentity,
                       domain: str = DOMAIN, prod_client: Optional[WorkspaceClient] = None,
                       dev_client: Optional[WorkspaceClient] = None) -> dict:
    """G6: a READ-ONLY preview of a prod->dev rehydrate, called BEFORE `rehydrate_space` so the UI
    can render an editable dev title + a table de-para for the caller to review/override first.

    Exports the source Space (gated the SAME way as `rehydrate_space`'s source-read: SOURCE guard
    via `assert_can_access`, deny before ever exporting — this function touches only that ONE blast
    site, since a preview never writes to dev at all) and returns its title plus every DISTINCT
    3-part ref it references (via `pre_render.find_refs`, including refs buried inside
    example/benchmark SQL), each with the plain prod_->dev_ default target `rehydrate_space` would
    use if the caller overrides nothing. `dev_suggestions` per table is best-effort (see
    `_dev_table_suggestions`) — an empty list there just means "no suggestions", never an error.

    Persists nothing (no store/promotion_id/audit here — recording happens once, in
    `rehydrate_space`, when the caller actually commits).
    """
    import app_logic  # local import: avoids a circular import at module load time

    prod = prod_client if prod_client is not None else app_logic._client()
    authz.assert_can_access(identity, source_prod_space_id, transport=prod)
    space = prod.genie.get_space(source_prod_space_id, include_serialized_space=True)
    ss = space.serialized_space
    raw = ss if isinstance(ss, str) else json.dumps(ss or {}, ensure_ascii=False)

    tables = []
    schemas: set = set()
    for ref in pre_render.find_refs(raw):
        default_target = pre_render.rebind(ref, "prod", "dev", domain)
        catalog, schema, _table = default_target.split(".")
        schemas.add((catalog, schema))
        tables.append({"source": ref, "default_target": default_target, "dev_suggestions": []})

    suggestions = _dev_table_suggestions(schemas, dev_client)
    for t in tables:
        catalog, schema, _table = t["default_target"].split(".")
        t["dev_suggestions"] = suggestions.get((catalog, schema), [])

    return {"title": getattr(space, "title", None), "tables": tables}

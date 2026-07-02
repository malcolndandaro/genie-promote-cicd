"""authz — the load-bearing authorization control (A2).

Two responsibilities, kept in ONE module because they're two halves of the same trust boundary:

1. ``verify_identity`` — turn an OBO access token into a **platform-verified** identity by asking
   the workspace itself "who does this token belong to" (``WorkspaceClient(token=...).current_user
   .me()``). This replaces the previous (broken) control, which trusted the proxy-supplied
   ``x-forwarded-email`` header as if it were an authorization input. That header IS forwarded by
   the Databricks Apps proxy and can't be spoofed on a genuinely proxied request, but the code
   docstrings that shipped with it were explicit that it must be "display only, NEVER an
   authorization input" — so this module gives every caller a real verification step instead of
   asking each call site to re-derive trust from a header. The verified identity is cached only
   for the lifetime of the object the caller holds (typically one request) — never module/process
   level — so a revoked/rotated token can't leave a stale identity authorizing later calls.

2. ``assert_can_access`` — the SINGLE reused per-user, per-action, NEVER-cached, FAIL-CLOSED guard
   that decides whether a verified identity may touch a given DEV Genie Space. Once the app is
   prod-hosted there is no forwarded dev token (OBO cannot span workspaces — ADR-0006), so every
   dev-side Genie operation runs as the standing dev-reader/writer service principal. That SP can
   reach EVERY dev Space by platform necessity (Genie has no per-Space delegation), which makes it
   a confused-deputy risk: without this guard, any authenticated app user could ask the app to read
   or overwrite ANY dev Space via the SP's broad reach. This guard is what makes that safe — see
   ``docs/security/assert-can-access-threat-model.md`` for the full threat model.

Both halves are used by every dev-touching endpoint (F2/F3/F4/F5 in a later epic, and today by
``app_logic.list_spaces`` / ``export_serialized`` / ``review_space``), so a fix here can't regress
feature-by-feature.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError

logger = logging.getLogger("authz")

# The Genie permission API's `request_object_type` (see PermissionsAPI.get) — confirmed live
# against dev (profile cerc-mlops-dev) during A2: `w.permissions.get(request_object_type="genie",
# request_object_id=<space_id>)` returns an `ObjectPermissions` with an `access_control_list` of
# `AccessControlResponse` entries (`user_name` | `group_name` | `service_principal_name`, each
# carrying `all_permissions: list[Permission]` with a `.permission_level` `PermissionLevel` enum).
GENIE_OBJECT_TYPE = "genie"

# Any of these permission levels means "may use the Space" for our purposes (read-export or
# write-overwrite are both gated the same way today — the guard answers "can this identity touch
# this Space at all", not which specific action). CAN_MANAGE/IS_OWNER/CAN_EDIT are the levels Genie
# actually issues; CAN_RUN/CAN_VIEW are included for forward-compat with lower-privilege grants.
_ACCESS_LEVELS = frozenset({
    "IS_OWNER", "CAN_MANAGE", "CAN_EDIT", "CAN_RUN", "CAN_VIEW",
})


class AccessDenied(Exception):
    """Raised by `assert_can_access` when the verified identity may not access the Space, OR when
    the ACL check itself could not be completed (fail-closed: uncertainty is denial, never access).
    """


@dataclass(frozen=True)
class VerifiedIdentity:
    """A platform-verified identity: the `user_name` (email) the OBO token's OWNER holds according
    to the workspace itself, plus their group memberships (also platform-attested, from the SAME
    `current_user.me()` call — no separate group-membership round trip needed). Immutable and
    intentionally NOT re-usable across requests: build a fresh one per request from that request's
    own token."""

    user_name: str
    group_names: frozenset[str]


def verify_identity(user_token: str, *, host: str | None = None) -> VerifiedIdentity:
    """Resolve the platform-verified identity behind an OBO access token.

    This is the ONLY legitimate way to turn a bearer token into an authorization-grade identity in
    this app: it asks the workspace "whose token is this" via `WorkspaceClient(token=...)
    .current_user.me()`, rather than trusting any client- or proxy-supplied header. Raises
    `AccessDenied` (fail-closed) if the token doesn't resolve — an expired/invalid/forged token
    must never silently resolve to "no identity" and then fall through to an admin-less, unscoped
    path; callers must treat a raised `AccessDenied` as "unauthenticated", not "anonymous ok".

    NOT cached beyond the returned object — call this once per request and thread the result
    through, never stash it keyed by token/session for reuse across requests (a revoked token must
    stop authorizing immediately, not just after some cache TTL).
    """
    try:
        w = WorkspaceClient(host=host, token=user_token, auth_type="pat")
        me = w.current_user.me()
    except DatabricksError as e:
        logger.info("verify_identity: token did not resolve to a user (%s)", type(e).__name__)
        raise AccessDenied("could not verify identity from the provided token") from e
    except Exception as e:  # noqa: BLE001 — fail closed even on an unrecognized failure mode
        logger.exception("verify_identity: unexpected error resolving identity")
        raise AccessDenied("could not verify identity from the provided token") from e
    if not me or not me.user_name:
        raise AccessDenied("token resolved to no user_name")
    groups = frozenset(g.display for g in (me.groups or []) if g and g.display)
    return VerifiedIdentity(user_name=me.user_name, group_names=groups)


def _principal_names(entry) -> set[str]:
    """The set of names an ACL entry matches against — a user's own name, OR (for a group entry)
    the group's name, which the caller compares against their verified group memberships."""
    names = set()
    if getattr(entry, "user_name", None):
        names.add(entry.user_name)
    if getattr(entry, "group_name", None):
        names.add(entry.group_name)
    if getattr(entry, "service_principal_name", None):
        names.add(entry.service_principal_name)
    return names


def _grants_access(entry) -> bool:
    for perm in (getattr(entry, "all_permissions", None) or []):
        level = getattr(perm, "permission_level", None)
        level_name = getattr(level, "value", level)  # enum -> str, or already a str
        if level_name in _ACCESS_LEVELS:
            return True
    return False


def assert_can_access(identity: VerifiedIdentity, space_id: str, *, transport: WorkspaceClient) -> None:
    """FAIL-CLOSED, NEVER-CACHED, live per-action check: may `identity` access the dev Genie Space
    `space_id`? Raises `AccessDenied` if not — including when the check itself can't be completed
    (ACL read error, timeout, malformed response, dev unreachable). This is deliberately a raise,
    not a bool return: a caller that forgets to check a bool return is a common way "fail closed"
    quietly becomes "fail open"; a raise can't be silently ignored.

    ``transport`` is the dev-reader/writer SP's WorkspaceClient (``app_logic._client(scope="dev-sp")``)
    — used ONLY to make the ACL-read API call ("transport only", per the PRD/ADR-0006: the SP's own
    broad reach is never itself treated as authorization for the calling user). The decision is
    made entirely from ``identity`` (a platform-VERIFIED user/groups) against the Space's ACL, never
    from anything the SP itself is separately allowed to do.

    Never cached: called again on every action needing it (the PRD explicitly calls out that a
    cache "reintroduces the false sense of control" this control replaces) — dev ACLs can change
    between calls (an admin revokes access; the caller must be re-denied on the very next call, not
    after some TTL expires).
    """
    try:
        perms = transport.permissions.get(request_object_type=GENIE_OBJECT_TYPE, request_object_id=space_id)
    except DatabricksError as e:
        # Fail closed: ANY error resolving the ACL (space not found, dev SP unreachable/ungranted,
        # dev workspace mid-wipe, transient API failure) denies access. We deliberately do NOT
        # distinguish "space doesn't exist" from "SP can't reach dev" from "user has no access"
        # here — all three must have the identical effect (deny) from this function's point of
        # view. (Disambiguating "SP needs re-bootstrapping" vs "user genuinely denied" for a
        # better error MESSAGE is a caller/UX concern — see SP2 — but the access decision itself
        # must be uniform denial.)
        logger.warning("assert_can_access: ACL check failed for space %s (%s) -> denying",
                        space_id, type(e).__name__)
        raise AccessDenied(f"could not verify access to space {space_id}") from e
    except Exception as e:  # noqa: BLE001 — genuinely unknown failure: still fail closed, not 500
        logger.exception("assert_can_access: unexpected error checking space %s -> denying", space_id)
        raise AccessDenied(f"could not verify access to space {space_id}") from e

    acl = perms.access_control_list or []
    caller_names = {identity.user_name} | set(identity.group_names)
    for entry in acl:
        if _principal_names(entry) & caller_names and _grants_access(entry):
            return  # explicit grant found for the user themself or one of their verified groups

    logger.info("assert_can_access: identity=%s denied on space=%s (no matching ACL grant)",
                identity.user_name, space_id)
    raise AccessDenied(f"{identity.user_name} may not access space {space_id}")


def is_admin(identity: Optional[VerifiedIdentity], admin_names: frozenset[str]) -> bool:
    """Whether a VERIFIED identity is a configured Steward/Admin. `admin_names` is the
    already-lowercased config-driven set (see `engine_api.main._admin_emails`); comparison is
    case-insensitive on the identity's own user_name. `identity=None` (no/invalid token) is never
    admin — fail closed."""
    return bool(identity) and identity.user_name.strip().lower() in admin_names

"""AccessSpec (F2) — the declared-access model captured at promotion time.

Models the TWO distinct access systems a promoted Genie Space touches, so neither is silently
dropped (F2 acceptance criteria):

  1. **Genie Space permissions** — who may open/run the Space in the Genie UI (CAN_RUN / CAN_VIEW).
  2. **UC data grants** — who may SELECT the underlying tables the Space queries.

Pure + serializable (no I/O): this module only defines the shape, JSON round-trip, and the derived
per-Space GROUP name (GRANT-02: access is applied via one group per Space, never scattered
per-user grants). It does NOT apply anything — see `scripts/apply_access.py` for the imperative,
idempotent enforcement that runs in CI as the prod SP (the declaration here is app-direct; the
enforcement is governed — F2 acceptance criteria).

Declared principals can be individual users OR existing groups; either way `check_grants` (GRANT-01)
verifies SELECT for every one of them, and the per-Space group is what's actually granted/added-to
in prod (individuals declared here are added as MEMBERS of the per-Space group by the enforcement
script, not granted directly).
"""
from __future__ import annotations

import dataclasses
import re

# Genie Space permission levels a Requester may declare (mirrors the levels `authz._ACCESS_LEVELS`
# treats as "may use the Space"; CAN_MANAGE/IS_OWNER are owner-level and out of scope for a
# consumer declaration — GRANT-02 keeps ownership on a group set up separately from this spec).
SPACE_PERMISSION_LEVELS = ("CAN_RUN", "CAN_VIEW")
DEFAULT_SPACE_PERMISSION = "CAN_RUN"

_GROUP_PREFIX = "grp_genie_"
_SAFE = re.compile(r"[^a-z0-9_]+")


@dataclasses.dataclass(frozen=True)
class Principal:
    """One declared principal (a user email or an existing group name)."""

    name: str
    is_group: bool = False


@dataclasses.dataclass(frozen=True)
class SpacePermission:
    """A Genie Space ACL entry to apply (system 1: Space permissions)."""

    principal: Principal
    level: str = DEFAULT_SPACE_PERMISSION

    def __post_init__(self):
        if self.level not in SPACE_PERMISSION_LEVELS:
            raise ValueError(f"unknown Space permission level {self.level!r}; "
                             f"expected one of {SPACE_PERMISSION_LEVELS}")


@dataclasses.dataclass(frozen=True)
class AccessSpec:
    """The Requester's declared access for one promotion.

    ``space_permissions`` -> system 1 (Genie Space CAN_RUN/CAN_VIEW).
    ``uc_principals``     -> system 2 (UC SELECT grants on the Space's data_source tables).

    Both lists are independent by design (a principal may need one, the other, or both — e.g. an
    embedding service account might need UC SELECT for a downstream job but never opens the Genie
    UI). `all_principals()` is the UNION, used by the extended GRANT-01 check (every declared
    principal — from either system — must be able to SELECT every data source; a principal that
    can open the Space but not read its data is exactly the GRANT-01 "unusable Space" failure this
    guards against).
    """

    space_permissions: tuple[SpacePermission, ...] = ()
    uc_principals: tuple[Principal, ...] = ()

    def all_principals(self) -> tuple[str, ...]:
        """Every declared principal name across BOTH systems, deduped, order-preserving."""
        seen: dict[str, None] = {}
        for sp in self.space_permissions:
            seen.setdefault(sp.principal.name, None)
        for p in self.uc_principals:
            seen.setdefault(p.name, None)
        return tuple(seen)

    def declared_groups(self) -> tuple[str, ...]:
        """Names of every declared principal that is a GROUP (not an individual user), deduped,
        order-preserving — across BOTH systems. G9: the only class whose UC-grantability needs
        verifying (`grant_check.is_uc_grantable_group`); an individual user is always account-level,
        so callers should never run that check against a declared user."""
        seen: dict[str, None] = {}
        for sp in self.space_permissions:
            if sp.principal.is_group:
                seen.setdefault(sp.principal.name, None)
        for p in self.uc_principals:
            if p.is_group:
                seen.setdefault(p.name, None)
        return tuple(seen)

    def is_empty(self) -> bool:
        return not self.space_permissions and not self.uc_principals

    def to_dict(self) -> dict:
        return {
            "space_permissions": [
                {"principal": sp.principal.name, "is_group": sp.principal.is_group, "level": sp.level}
                for sp in self.space_permissions
            ],
            "uc_principals": [
                {"principal": p.name, "is_group": p.is_group} for p in self.uc_principals
            ],
        }

    @staticmethod
    def from_dict(d: dict | None) -> "AccessSpec":
        d = d or {}
        space_permissions = tuple(
            SpacePermission(
                principal=Principal(name=sp["principal"], is_group=bool(sp.get("is_group", False))),
                level=sp.get("level", DEFAULT_SPACE_PERMISSION),
            )
            for sp in (d.get("space_permissions") or [])
            if sp.get("principal")
        )
        uc_principals = tuple(
            Principal(name=p["principal"], is_group=bool(p.get("is_group", False)))
            for p in (d.get("uc_principals") or [])
            if p.get("principal")
        )
        return AccessSpec(space_permissions=space_permissions, uc_principals=uc_principals)


def group_name_for(slug: str) -> str:
    """The stable, deterministic per-Space GROUP name (GRANT-02: apply access via one group per
    Space, never scattered per-user grants). Config-driven prefix, derived purely from the space's
    existing slug (no new identifier to keep in sync) — safe as a Databricks account group display
    name (lowercase alnum/underscore)."""
    safe = _SAFE.sub("_", slug.lower()).strip("_") or "space"
    return f"{_GROUP_PREFIX}{safe}"

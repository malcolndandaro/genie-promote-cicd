"""Canonical Público do Space declaration used by the pilot promotion contract.

AudienceSpec is deliberately one-system: every declared principal receives Genie ``CAN_RUN``.
It never carries a permission level or a Unity Catalog grant request.  The small legacy translator
is read-only and exists only for the expand/switch compatibility window (ADR-0009).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable


class LegacyAudienceError(ValueError):
    """A legacy AccessSpec cannot be translated without changing its meaning."""


@dataclasses.dataclass(frozen=True)
class AudiencePrincipal:
    name: str
    is_group: bool = False

    def __post_init__(self) -> None:
        normalized = self.name.strip()
        if not normalized:
            raise ValueError("audience principal is required")
        object.__setattr__(self, "name", normalized)


@dataclasses.dataclass(frozen=True)
class AudienceSpec:
    """Required, deterministic desired set of app-managed Genie audience principals."""

    principals: tuple[AudiencePrincipal, ...]

    def __post_init__(self) -> None:
        if not self.principals:
            raise ValueError("AudienceSpec requires at least one principal")
        seen: set[str] = set()
        ordered: list[AudiencePrincipal] = []
        for principal in self.principals:
            key = principal.name.casefold()
            if key in seen:
                raise ValueError(f"duplicate audience principal {principal.name!r}")
            seen.add(key)
            ordered.append(principal)
        ordered.sort(key=lambda p: (p.name.casefold(), p.is_group, p.name))
        object.__setattr__(self, "principals", tuple(ordered))

    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.principals)

    def to_dict(self) -> dict:
        return {
            "principals": [
                {"principal": p.name, "is_group": p.is_group}
                for p in self.principals
            ]
        }

    @staticmethod
    def from_dict(value: dict | None) -> "AudienceSpec":
        if not isinstance(value, dict):
            raise ValueError("AudienceSpec must be an object")
        raw = value.get("principals")
        if not isinstance(raw, list):
            raise ValueError("AudienceSpec.principals must be a list")
        principals: list[AudiencePrincipal] = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("every audience principal must be an object")
            if "level" in entry or "permission_level" in entry:
                raise ValueError("AudienceSpec does not accept a permission level; it always derives CAN_RUN")
            principals.append(AudiencePrincipal(
                name=str(entry.get("principal") or ""),
                is_group=bool(entry.get("is_group", False)),
            ))
        return AudienceSpec(tuple(principals))


def from_legacy_access_spec(
    value: dict | None,
    *,
    warn: Callable[[str], None] | None = None,
) -> AudienceSpec:
    """Translate only the safe, lossless part of a legacy AccessSpec.

    ``CAN_RUN`` Space entries become AudienceSpec principals. ``CAN_VIEW`` (or any other level) is
    rejected rather than upgraded. UC principals are discarded with a loud warning and are never
    mutated. The result is still required/non-empty.
    """
    if not isinstance(value, dict):
        raise LegacyAudienceError("legacy AccessSpec must be an object")
    principals: list[AudiencePrincipal] = []
    for entry in value.get("space_permissions") or []:
        if not isinstance(entry, dict):
            raise LegacyAudienceError("legacy space permission must be an object")
        level = entry.get("level", "CAN_RUN")
        if level != "CAN_RUN":
            raise LegacyAudienceError(
                f"legacy permission {level!r} cannot be translated; AudienceSpec only supports CAN_RUN"
            )
        principals.append(AudiencePrincipal(
            name=str(entry.get("principal") or ""),
            is_group=bool(entry.get("is_group", False)),
        ))
    discarded = value.get("uc_principals") or []
    if discarded and warn:
        warn(
            f"legacy AccessSpec contained {len(discarded)} UC principal(s); "
            "discarded because AudienceSpec never grants Unity Catalog access"
        )
    try:
        return AudienceSpec(tuple(principals))
    except ValueError as exc:
        raise LegacyAudienceError(str(exc)) from exc


def parse_sidecar(value: dict, *, warn: Callable[[str], None] | None = None) -> AudienceSpec:
    """Read canonical AudienceSpec or the narrow read-only legacy shape."""
    if "principals" in value:
        return AudienceSpec.from_dict(value)
    return from_legacy_access_spec(value, warn=warn)

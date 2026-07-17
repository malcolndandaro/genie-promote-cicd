"""Canonical Público do Space declaration used by the pilot promotion contract.

AudienceSpec is deliberately one-system: every declared principal receives Genie ``CAN_RUN``.
It never carries a permission level or a Unity Catalog grant request.
"""
from __future__ import annotations

import dataclasses


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
def parse_sidecar(value: dict) -> AudienceSpec:
    """Parse the canonical committed sidecar contract."""
    return AudienceSpec.from_dict(value)

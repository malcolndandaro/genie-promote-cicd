"""business_area — the CONTROLLED vocabulary of business areas a promoted resource is filed under.

A promoted dashboard lives at `src/dashboards/<area>/<name>/`, where `<area>` is the owning business
area (risco, compliance, comercial…) rather than the data domain. That grouping reflects the ORG, which
is what a business author actually thinks in — but a free-text path segment would rot immediately
("risco", "Risco", "risk", "rsico" all becoming distinct directories nobody can find anything in).

So the area is a CLOSED SET, validated at every boundary:
  - the app only ever offers these values in its picker (never a text input);
  - the promotion request is REFUSED for an unknown area, before any git write;
  - CI re-validates the committed path, so a hand-edited content PR fails the required check too.

Config-driven (ADR-0004): the set itself is deployment configuration, not code. `APP_BUSINESS_AREAS`
overrides it as JSON (`[{"key": "...", "label": "..."}]`) so a fork files resources under its own
org's areas without editing this module. The default below is CERC's.

Pure and I/O-free — importable by the app, the CI scripts and the deploy alike.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re

# A path/branch/DABs-identifier-safe segment: lowercase, starts with a letter, no separators. This is
# deliberately stricter than the filesystem allows — the value becomes a git branch segment AND part of
# a DABs resource key, so anything that could need quoting or escaping is rejected outright.
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


@dataclasses.dataclass(frozen=True)
class BusinessArea:
    """One filing area: a safe `key` for the path, and a human `label` for the UI."""

    key: str
    label: str

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key):
            raise ValueError(
                f"invalid business area key {self.key!r}: use 2-32 chars, lowercase letters, digits "
                "and underscore, starting with a letter")
        if not self.label.strip():
            raise ValueError(f"business area {self.key!r} requires a label")


# CERC's areas. Sample configuration, not a binding: a fork sets APP_BUSINESS_AREAS instead.
_DEFAULT_AREAS = (
    BusinessArea("risco", "Risco"),
    BusinessArea("compliance", "Compliance"),
    BusinessArea("comercial", "Comercial"),
    BusinessArea("operacoes", "Operações"),
    BusinessArea("financeiro", "Financeiro"),
    BusinessArea("dados", "Dados & Plataforma"),
)


def _configured() -> "tuple[BusinessArea, ...]":
    """The effective area set: `APP_BUSINESS_AREAS` if set and parseable, else the default.

    A MALFORMED override falls back to the default rather than raising: this is read on every request,
    and a typo in deployment config must not take the whole promotion surface down. An override with a
    single invalid entry is rejected WHOLE (not partially applied), so the operator sees the default and
    a clear discrepancy rather than a silently half-applied list.
    """
    raw = os.environ.get("APP_BUSINESS_AREAS")
    if not raw:
        return _DEFAULT_AREAS
    try:
        parsed = json.loads(raw)
        areas = tuple(BusinessArea(key=str(a["key"]), label=str(a["label"])) for a in parsed)
    except (ValueError, TypeError, KeyError, IndexError):
        return _DEFAULT_AREAS
    return areas or _DEFAULT_AREAS


def all_areas() -> "tuple[BusinessArea, ...]":
    """Every area, in configured order — the exact list the UI picker renders."""
    return _configured()


def keys() -> "frozenset[str]":
    return frozenset(a.key for a in _configured())


def get(key: "str | None") -> BusinessArea:
    """Resolve an area key, raising `ValueError` on anything not in the controlled set.

    Fails CLOSED and never defaults: filing a resource under the wrong area is a governance problem
    (it decides who finds it and who reviews it), so an absent or unknown area is a refusal, not a
    silent fallback to some 'other' bucket.
    """
    resolved = (key or "").strip()
    if not resolved:
        raise ValueError(
            f"a business area is required; choose one of {sorted(keys())}")
    for area in _configured():
        if area.key == resolved:
            return area
    raise ValueError(
        f"unknown business area {resolved!r}; expected one of {sorted(keys())}")


def resource_name(title: str) -> str:
    """Derive a path-safe resource NAME from a human production title.

    `Painel de Recebíveis — Volume por Bandeira` -> `painel_de_recebiveis_volume_por_bandeira`.

    This is the second half of a nested slug (`<area>/<name>`), so it must satisfy the same
    constraints as an area key: it becomes a git branch segment and part of a DABs resource key.
    Accents are folded rather than stripped (`Recebíveis` -> `recebiveis`) so the name stays readable
    to a Portuguese-speaking author browsing the repo, which is the whole reason the layout is
    human-meaningful instead of id-based.

    Raises `ValueError` when a title yields nothing usable (e.g. only punctuation): the caller must
    then ask for a different title rather than commit an empty path segment.
    """
    import unicodedata

    folded = unicodedata.normalize("NFKD", title or "")
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    collapsed = re.sub(r"_{2,}", "_", collapsed)
    # Keep it addressable: a very long title would make an unwieldy branch name and path.
    trimmed = collapsed[:48].strip("_")
    if not trimmed or not trimmed[:1].isalpha():
        raise ValueError(
            f"could not derive a resource name from title {title!r} — it must contain at least one "
            "letter; rename the resource and try again")
    return trimmed


def label_for(key: str) -> str:
    """The human label for an area key, or the key itself when it is no longer configured.

    Display-only tolerance: an area REMOVED from the config after a resource was filed under it must
    still render in history rather than crashing the page. `get()` remains strict for writes.
    """
    for area in _configured():
        if area.key == key:
            return area.label
    return key

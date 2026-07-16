#!/usr/bin/env python3
"""Map changed content paths to the Genie Space slugs whose contract must be checked."""
from __future__ import annotations

import re
import sys

_PATH = re.compile(
    r"^src/genie/(?P<slug>.+?)\."
    r"(?:serialized_space\.json|title|mapping\.json|audience\.json|revision\.json|access\.json)$"
)


def changed_slugs(paths: list[str]) -> list[str]:
    return sorted({match.group("slug") for path in paths
                   if (match := _PATH.fullmatch(path.strip()))})


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv else sys.stdin.read().splitlines()
    print(" ".join(changed_slugs(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

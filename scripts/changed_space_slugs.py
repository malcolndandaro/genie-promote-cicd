#!/usr/bin/env python3
"""Map changed content paths to the resource slugs whose contract must be checked.

Scoping the governance gates to the resources a PR ACTUALLY changes is what stops legacy content
(promoted before a gate existed) from blocking an unrelated PR.

Two kinds, two path families, ONE script: pass `--kind dashboard` for the AI/BI dashboard paths. The
DEFAULT is Genie, so every existing workflow invocation is unchanged — including the invariant that a
dashboard path yields NO Genie slug (a dashboard must never be fed to the Genie gates).
"""
from __future__ import annotations

import re
import sys

_SIDECARS = r"title|mapping\.json|audience\.json|revision\.json|access\.json"

_PATHS = {
    "genie_space": re.compile(rf"^src/genie/(?P<slug>.+?)\.(?:serialized_space\.json|{_SIDECARS})$"),
    "dashboard": re.compile(rf"^src/dashboards/(?P<slug>.+?)\.(?:lvdash\.json|{_SIDECARS})$"),
}


def changed_slugs(paths: list[str], kind: str = "genie_space") -> list[str]:
    """The slugs of ``kind`` touched by ``paths`` (sorted, de-duplicated)."""
    pattern = _PATHS[kind]
    return sorted({match.group("slug") for path in paths
                   if (match := pattern.fullmatch(path.strip()))})


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    kind = "genie_space"
    if "--kind" in args:
        idx = args.index("--kind")
        kind = args[idx + 1] if len(args) > idx + 1 else kind
        del args[idx:idx + 2]
    if kind not in _PATHS:
        print(f"unknown kind {kind!r}; expected one of {sorted(_PATHS)}", file=sys.stderr)
        return 2
    paths = args if args else sys.stdin.read().splitlines()
    print(" ".join(changed_slugs(paths, kind)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

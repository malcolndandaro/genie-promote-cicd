#!/usr/bin/env python3
"""Compute the immutable revision of the overlaid content tree (ADR-0008)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "genie_reviewer"))

import change_request  # noqa: E402


def compute_content_tree_revision(root: Path) -> str:
    root = root.resolve()
    files = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted((root / "src").rglob("*"))
        if path.is_file() and not path.name.endswith(".revision.json")
    }
    return change_request.compute_content_revision(files)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else Path.cwd()
    print(compute_content_tree_revision(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

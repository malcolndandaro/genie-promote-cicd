"""Small provider-workflow helpers shared by deployment scripts."""
from __future__ import annotations


def gh_escape(value: str) -> str:
    """Escape one GitHub Actions workflow-command payload."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def resolve_space_id(workspace, title: str) -> str:
    """Resolve exactly one live Genie Space by title; never guess."""
    if not title:
        raise ValueError("no title provided; cannot resolve the deployed Genie Space")
    matches = [
        space.space_id
        for space in (workspace.genie.list_spaces().spaces or [])
        if (space.title or "") == title
    ]
    if not matches:
        raise ValueError(f"no deployed Genie Space found with title {title!r}")
    if len(matches) > 1:
        raise ValueError(
            f"{len(matches)} deployed Genie Spaces share title {title!r} "
            f"(ids={matches}); refusing to guess"
        )
    return matches[0]

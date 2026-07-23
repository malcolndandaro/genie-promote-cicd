"""Small provider-workflow helpers shared by deployment scripts."""
from __future__ import annotations

import time
from typing import Callable


_SPACE_RESOLVE_MAX_ATTEMPTS = 30
_SPACE_RESOLVE_RETRY_SECONDS = 2.0


def gh_escape(value: str) -> str:
    """Escape one GitHub Actions workflow-command payload."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def resolve_space_id(
    workspace,
    title: str,
    *,
    max_attempts: int = _SPACE_RESOLVE_MAX_ATTEMPTS,
    retry_seconds: float = _SPACE_RESOLVE_RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Resolve one live Genie Space by title, allowing bounded post-deploy propagation."""
    if not title:
        raise ValueError("no title provided; cannot resolve the deployed Genie Space")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(max_attempts):
        matches = [
            space.space_id
            for space in (workspace.genie.list_spaces().spaces or [])
            if (space.title or "") == title
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} deployed Genie Spaces share title {title!r} "
                f"(ids={matches}); refusing to guess"
            )
        if attempt + 1 < max_attempts:
            sleep(retry_seconds)
    raise ValueError(f"no deployed Genie Space found with title {title!r}")

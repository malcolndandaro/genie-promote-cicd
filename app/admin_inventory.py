"""admin_inventory — F4: the pure join between LIVE prod Genie Spaces and the app's own governance
records (the Lakebase promotion index + declared AccessSpec), for the admin console's inventory
view (US-27/US-30).

Kept as its own small, dependency-injected module (no FastAPI, no SDK import at call time beyond
what the caller passes in) so the join logic — the actual acceptance-criteria-bearing behavior — is
unit-tested OFFLINE with fakes, mirroring every other module in this app (`review_core`,
`grant_check`, `reconcile`). The endpoint (`engine_api/main.py`) supplies:

  - ``list_prod_spaces``: a zero-arg callable returning ``[{"space_id", "title"}, ...]`` for
    EVERY Space currently deployed in prod, called AT READ TIME (never a cache) so the inventory
    reflects live prod state (F4 acceptance criteria: "not stale"). In production this is
    ``app_logic.list_spaces()`` run with the app's OWN prod-local identity (no OBO, no dev-sp — the
    app itself lives in the prod workspace per ADR-0006, so this is a same-workspace call).
  - ``promotions``: every `Promotion` the durable store knows about (`store.list_promotions(None)`)
    — used to attribute an owner/phase/declared-access to a live Space, by matching on
    ``resource_id`` (the space_id) OR, failing that, on title (a promotion's `resource_id` might
    reference a DIFFERENT workspace/generation of "the same" Space after a rehydrate/recreate; title
    is a softer fallback signal, never used to override an id match).

Two edge cases the acceptance criteria call out explicitly, both handled without raising:
  - A live prod Space with NO matching Promotion record → still listed, with owner/access/phase as
    ``None`` (rendered "—" by the UI) rather than being dropped or crashing the join.
  - A Promotion whose Space is gone from prod (deleted/renamed) → surfaced in a SEPARATE list
    (``orphaned_promotions``) rather than silently disappearing, so an admin can tell "deployed but
    unaccounted for" apart from "recorded but no longer live".
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Iterable, Optional, Protocol


@dataclasses.dataclass(frozen=True)
class InventoryRow:
    """One live prod Space, joined with whatever governance record (if any) matches it."""

    space_id: str
    title: str
    owner: Optional[str]              # Promotion.requester_email (display-only OBO email), or None
    phase: Optional[str]              # Promotion.current_phase, or None if no matching Promotion
    access_spec: Optional[dict]       # the declared AccessSpec dict, or None
    promotion_id: Optional[str]       # the matched Promotion's id, for a deep-link, or None
    terminal: Optional[bool]          # the matched Promotion's terminal flag, or None


@dataclasses.dataclass(frozen=True)
class OrphanedPromotion:
    """A Promotion whose target Space is no longer visible in the live prod listing (deleted,
    renamed, or the prod read itself is scoped differently) — surfaced distinctly so it reads as
    "recorded but not currently live", never silently dropped or conflated with a live row."""

    promotion_id: str
    resource_id: str
    resource_title: Optional[str]
    owner: Optional[str]
    phase: Optional[str]


class _PromotionLike(Protocol):
    id: str
    resource_id: str
    resource_title: Optional[str]
    requester_email: Optional[str]
    current_phase: Optional[str]
    terminal: bool
    access_spec: Optional[dict]
    updated_at: object


def _norm_title(t: Optional[str]) -> str:
    return (t or "").strip().lower()


def build_inventory(live_spaces: Iterable[dict], promotions: Iterable[_PromotionLike]
                     ) -> tuple[list[InventoryRow], list[OrphanedPromotion]]:
    """The pure join (no I/O): every live prod Space, decorated with the BEST matching Promotion's
    owner/phase/declared-access, plus the list of Promotions whose Space isn't live anymore.

    Matching: first by ``resource_id == space_id`` (exact — the same Space that was promoted).
    Within a resource_id, several Promotions can exist (a re-promotion opens a new PR sometimes, or
    history accumulates) — the most RECENTLY updated one wins, so the inventory always reflects the
    latest governance state for that Space, not an arbitrary/oldest record. Falls back to a
    case-insensitive TITLE match only when no id match exists (e.g. the Space was rehydrated/
    recreated under a new id but the title is unchanged) — an id match always wins over a title
    match, and a title match never overrides a more specific id match found for another row.

    A live Space with no match of either kind still gets a row (owner/phase/access all None) — never
    dropped. A Promotion that matches no live Space is returned in the second list, never raised
    as an error and never merged into the first list under a synthetic entry.
    """
    promos = list(promotions)
    by_id: dict[str, list[_PromotionLike]] = {}
    by_title: dict[str, list[_PromotionLike]] = {}
    for p in promos:
        by_id.setdefault(p.resource_id, []).append(p)
        if p.resource_title:
            by_title.setdefault(_norm_title(p.resource_title), []).append(p)

    def _latest(cands: list[_PromotionLike]) -> _PromotionLike:
        return max(cands, key=lambda p: p.updated_at)

    matched_promotion_ids: set[str] = set()
    rows: list[InventoryRow] = []
    for s in live_spaces:
        space_id = s.get("space_id") or s.get("id")
        title = s.get("title") or "(sem título)"
        cands = by_id.get(space_id) or by_title.get(_norm_title(title)) or []
        match = _latest(cands) if cands else None
        if cands:
            # EVERY candidate for this live space is accounted for, not just the winning (most
            # recently updated) one — an older Promotion for a Space that's still live is
            # SUPERSEDED, not orphaned/gone, so it must not appear in `orphaned`.
            matched_promotion_ids.update(p.id for p in cands)
        rows.append(InventoryRow(
            space_id=space_id, title=title,
            owner=match.requester_email if match else None,
            phase=match.current_phase if match else None,
            access_spec=match.access_spec if match else None,
            promotion_id=match.id if match else None,
            terminal=match.terminal if match else None,
        ))

    orphaned = [
        OrphanedPromotion(
            promotion_id=p.id, resource_id=p.resource_id, resource_title=p.resource_title,
            owner=p.requester_email, phase=p.current_phase)
        for p in promos if p.id not in matched_promotion_ids
    ]
    return rows, orphaned


def load_inventory(list_prod_spaces: Callable[[], list[dict]],
                    promotions: Iterable[_PromotionLike]) -> dict:
    """Read live prod Spaces (via the injected, zero-arg ``list_prod_spaces`` — called fresh on
    EVERY invocation, never memoized here or by this module, so the endpoint that wires this up is
    the only place that could introduce staleness, and it doesn't) and join them.

    Returns a JSON-shaped dict (not dataclasses) so the endpoint can return it directly.
    """
    rows, orphaned = build_inventory(list_prod_spaces(), promotions)
    return {
        "spaces": [dataclasses.asdict(r) for r in rows],
        "orphaned_promotions": [dataclasses.asdict(o) for o in orphaned],
    }

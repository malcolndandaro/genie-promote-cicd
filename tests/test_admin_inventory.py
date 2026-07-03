"""Offline unit tests for admin_inventory (F4) — the pure live-spaces/promotions join, NO Databricks
SDK, NO live workspace. `build_inventory` takes plain dicts + fake Promotion-shaped objects so the
join logic (the actual acceptance-criteria-bearing behavior) is verified independent of any
transport.
"""
import dataclasses
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from admin_inventory import build_inventory, load_inventory  # noqa: E402


@dataclasses.dataclass
class _FakePromotion:
    id: str
    resource_id: str
    resource_title: str | None
    requester_email: str | None
    current_phase: str | None
    terminal: bool
    access_spec: dict | None
    updated_at: datetime


def _promo(**kw) -> _FakePromotion:
    defaults = dict(
        id="p1", resource_id="s1", resource_title="Recebíveis", requester_email="ana@x",
        current_phase="deployed", terminal=True, access_spec=None,
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(kw)
    return _FakePromotion(**defaults)


def test_live_space_with_matching_promotion_is_decorated():
    spaces = [{"space_id": "s1", "title": "Recebíveis"}]
    promos = [_promo()]
    rows, orphaned = build_inventory(spaces, promos)
    assert orphaned == []
    assert len(rows) == 1
    r = rows[0]
    assert r.space_id == "s1" and r.title == "Recebíveis"
    assert r.owner == "ana@x" and r.phase == "deployed" and r.promotion_id == "p1"
    assert r.terminal is True


def test_live_space_with_no_promotion_still_appears_with_nulls():
    """Acceptance: a prod Space with no promotion record still appears (owner/access '-')."""
    spaces = [{"space_id": "orphan-space", "title": "Sem dono"}]
    rows, orphaned = build_inventory(spaces, [])
    assert orphaned == []
    assert len(rows) == 1
    r = rows[0]
    assert r.space_id == "orphan-space"
    assert r.owner is None and r.phase is None and r.access_spec is None and r.promotion_id is None


def test_promotion_whose_space_is_gone_is_distinguishable_not_crashing():
    """Acceptance: a promotion whose Space is gone from prod doesn't crash the join and is
    surfaced distinctly (not merged into the live rows list)."""
    promos = [_promo(id="p-gone", resource_id="deleted-space")]
    rows, orphaned = build_inventory([], promos)
    assert rows == []
    assert len(orphaned) == 1
    assert orphaned[0].promotion_id == "p-gone" and orphaned[0].resource_id == "deleted-space"
    assert orphaned[0].owner == "ana@x" and orphaned[0].phase == "deployed"


def test_mixed_live_matched_live_unmatched_and_orphaned_all_at_once():
    spaces = [
        {"space_id": "s1", "title": "Recebíveis"},        # matches p1
        {"space_id": "s-new", "title": "Novo Espaço"},     # no promotion at all
    ]
    promos = [_promo(id="p1", resource_id="s1"), _promo(id="p-gone", resource_id="s-gone")]
    rows, orphaned = build_inventory(spaces, promos)
    by_id = {r.space_id: r for r in rows}
    assert by_id["s1"].promotion_id == "p1"
    assert by_id["s-new"].owner is None
    assert [o.promotion_id for o in orphaned] == ["p-gone"]


def test_multiple_promotions_for_same_space_the_most_recently_updated_wins():
    older = _promo(id="p-old", resource_id="s1", current_phase="open",
                   updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = _promo(id="p-new", resource_id="s1", current_phase="deployed",
                   updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=1))
    rows, orphaned = build_inventory([{"space_id": "s1", "title": "Recebíveis"}], [older, newer])
    assert len(rows) == 1 and rows[0].promotion_id == "p-new" and rows[0].phase == "deployed"
    # The older promotion for the SAME live space is not "gone" — it's superseded, not orphaned.
    assert orphaned == []


def test_title_fallback_match_when_no_resource_id_match():
    """A Space rehydrated/recreated under a new id can still be attributed via a case-insensitive
    title match, but ONLY when no id match exists for that space_id."""
    promo = _promo(id="p1", resource_id="old-id-9999", resource_title="Recebíveis Antecipados")
    rows, orphaned = build_inventory(
        [{"space_id": "new-id-123", "title": "recebíveis antecipados"}], [promo])
    assert len(rows) == 1 and rows[0].promotion_id == "p1"
    assert orphaned == []  # matched by title, so not reported as orphaned


def test_id_match_always_wins_over_a_title_match_for_a_different_row():
    """An id match for one live space must not be "stolen" by a looser title match intended for
    another row — id match takes priority whenever it exists for that space_id."""
    exact = _promo(id="p-exact", resource_id="s1", resource_title="Recebíveis")
    title_only = _promo(id="p-title", resource_id="does-not-exist", resource_title="Recebíveis")
    rows, orphaned = build_inventory([{"space_id": "s1", "title": "Recebíveis"}], [exact, title_only])
    assert len(rows) == 1 and rows[0].promotion_id == "p-exact"
    # title_only never matched any live row -> correctly orphaned, not silently absorbed
    assert [o.promotion_id for o in orphaned] == ["p-title"]


def test_empty_prod_and_empty_promotions_returns_empty_lists_no_crash():
    rows, orphaned = build_inventory([], [])
    assert rows == [] and orphaned == []


def test_load_inventory_calls_the_injected_reader_fresh_every_time():
    """F4 acceptance: the inventory reflects LIVE prod state — `load_inventory` must call the
    injected reader on every invocation, never cache/memoize it itself."""
    calls = {"n": 0}

    def reader():
        calls["n"] += 1
        return [{"space_id": "s1", "title": "Recebíveis"}]

    load_inventory(reader, [_promo()])
    load_inventory(reader, [_promo()])
    assert calls["n"] == 2  # called once per load_inventory call — no caching in this module

    result = load_inventory(reader, [_promo()])
    assert result["spaces"][0]["space_id"] == "s1"
    assert result["spaces"][0]["owner"] == "ana@x"
    assert result["orphaned_promotions"] == []

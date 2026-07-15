"""Unit tests for the F4 admin console endpoints (engine_api/main.py): live prod inventory,
cross-Promotion audit, and the full access-request queue. FastAPI TestClient + fakes — no network,
no live workspace.

Mirrors tests/test_engine_api.py's `_verify_as` convention (fakes `authz.verify_identity` so admin
gating is exercised against a platform-VERIFIED identity under test control, never a spoofable
display header) and reuses the SAME `PromotionStore`/`AccessRequestStore` InMemoryBackend fixtures
those sibling test files already use, wired into `engine_api.app.state`.

Verifies: (1) every F4 endpoint 403s a non-admin (and a caller with NO token at all — F4 has no
"local/offline" degraded mode, unlike scope=mine reads); (2) a spoofed `x-forwarded-email` display
header cannot forge admin access; (3) the inventory endpoint calls the injected live-space reader
FRESH on every request (proving "not stale") and correctly reflects the no-promotion / orphaned-
promotion edge cases through the real HTTP layer; (4) the audit aggregation is cross-Promotion,
newest-first, and carries the acting identity + which Promotion/resource it belongs to; (5) the
admin access-request queue returns every state, not just `requested`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import authz  # noqa: E402
import main as engine_api  # noqa: E402
import pytest  # noqa: E402
from access_request_store import AccessRequestStore
from access_request_store import InMemoryBackend as AccessInMemoryBackend  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from promotion_store import InMemoryBackend as PromotionInMemoryBackend  # noqa: E402
from promotion_store import PromotionStore  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


@pytest.fixture()
def store():
    s = PromotionStore(PromotionInMemoryBackend())
    engine_api.app.state.store = s
    yield s
    engine_api.app.state.store = None


@pytest.fixture()
def access_store():
    s = AccessRequestStore(AccessInMemoryBackend())
    engine_api.app.state.access_store = s
    yield s
    engine_api.app.state.access_store = None


ADMIN_HEADERS = {"x-forwarded-access-token": "tok-admin", "x-forwarded-email": "admin@x"}
RANDO_HEADERS = {"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "rando@x"}


def _setup_admin(monkeypatch):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-admin": "admin@x", "tok-rando": "rando@x", "tok-ana": "ana@x"})


# --- gating: every F4 endpoint is admin-only, server-side, via the VERIFIED identity ------------

ADMIN_ENDPOINTS = ("/api/admin/inventory", "/api/admin/access-requests", "/api/admin/audit",
                  "/api/admin/rehydrate-events")


@pytest.mark.parametrize("path", ADMIN_ENDPOINTS)
def test_admin_endpoints_403_a_non_admin(monkeypatch, store, access_store, path):
    _setup_admin(monkeypatch)
    r = client.get(path, headers=RANDO_HEADERS)
    assert r.status_code == 403


@pytest.mark.parametrize("path", ADMIN_ENDPOINTS)
def test_admin_endpoints_403_with_no_token_at_all(monkeypatch, store, access_store, path):
    # Unlike scope=mine, F4 has no local/offline degraded mode: a missing OBO token verifies to no
    # identity and must be denied, never treated as "skip the check".
    _setup_admin(monkeypatch)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", ADMIN_ENDPOINTS)
def test_admin_endpoints_ignore_a_spoofed_display_header(monkeypatch, store, access_store, path):
    _setup_admin(monkeypatch)
    r = client.get(path, headers={"x-forwarded-access-token": "tok-rando", "x-forwarded-email": "admin@x"})
    assert r.status_code == 403  # the VERIFIED identity (rando) is what's checked, not the header


@pytest.mark.parametrize("path", ADMIN_ENDPOINTS)
def test_admin_endpoints_allow_a_real_admin(monkeypatch, store, access_store, path):
    _setup_admin(monkeypatch)
    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces", lambda *a, **k: [])
    assert client.get(path, headers=ADMIN_HEADERS).status_code == 200


# --- /api/admin/inventory -------------------------------------------------------------------------


def _promote(monkeypatch, *, space_id, number, requester_headers, title=None):
    def fake(space_id_, *a, **k):
        return {"review": {"gate": {"conclusion": "success", "summary": "ok"}, "findings": [],
                           "eval": {"status": "pass", "summary": "n/a"}, "timeline": []},
                "pr": {"number": number, "url": f"https://gh/pr/{number}"}}

    monkeypatch.setattr(engine_api.app_logic, "request_promotion", fake)
    body = {"space_id": space_id}
    if title:
        body["resource_title"] = title
    r = client.post("/api/promote", json=body, headers=requester_headers)
    assert r.status_code == 200
    return r.json()["promotion_id"]


def test_inventory_joins_live_spaces_with_the_promotion_index(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers, title="Recebíveis")

    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces",
                        lambda *a, **k: [{"space_id": "s1", "title": "Recebíveis"}])
    r = client.get("/api/admin/inventory", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert len(body["spaces"]) == 1
    row = body["spaces"][0]
    assert row["space_id"] == "s1" and row["owner"] == "ana@x" and row["phase"] == "open"
    assert body["orphaned_promotions"] == []


def test_inventory_live_space_with_no_promotion_still_appears(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces",
                        lambda *a, **k: [{"space_id": "unowned", "title": "Ninguém promoveu"}])
    body = client.get("/api/admin/inventory", headers=ADMIN_HEADERS).json()
    assert len(body["spaces"]) == 1
    row = body["spaces"][0]
    assert row["space_id"] == "unowned" and row["owner"] is None and row["phase"] is None


def test_inventory_promotion_whose_space_is_gone_is_reported_orphaned_not_crashed(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    pid = _promote(monkeypatch, space_id="deleted-space", number=2, requester_headers=ana_headers)

    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces", lambda *a, **k: [])  # nothing live
    r = client.get("/api/admin/inventory", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["spaces"] == []
    assert len(body["orphaned_promotions"]) == 1
    assert body["orphaned_promotions"][0]["promotion_id"] == pid
    assert body["orphaned_promotions"][0]["resource_id"] == "deleted-space"


def test_inventory_calls_live_reader_fresh_every_request_not_cached(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    calls = {"n": 0}

    def reader(*a, **k):
        calls["n"] += 1
        return [{"space_id": f"s{calls['n']}", "title": "x"}]

    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces", reader)
    first = client.get("/api/admin/inventory", headers=ADMIN_HEADERS).json()
    second = client.get("/api/admin/inventory", headers=ADMIN_HEADERS).json()
    assert calls["n"] == 2  # one live call per request -- never served from a cache
    assert first["spaces"][0]["space_id"] == "s1"
    assert second["spaces"][0]["space_id"] == "s2"  # reflects the SECOND (changed) live read


def test_inventory_requires_the_store(monkeypatch):
    _setup_admin(monkeypatch)
    engine_api.app.state.store = None
    monkeypatch.setattr(engine_api.app_logic, "list_prod_spaces", lambda *a, **k: [])
    assert client.get("/api/admin/inventory", headers=ADMIN_HEADERS).status_code == 503


# --- /api/admin/access-requests: full queue, all states -------------------------------------------


def test_admin_access_request_queue_shows_every_state(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    r1 = access_store.create_request(space_id="s1", space_title="A", requester_email="ana@x")
    r2 = access_store.create_request(space_id="s2", space_title="B", requester_email="bob@x")
    access_store.decide(r2.id, approve=False, approver_email="admin@x", decision_note="fora do escopo")  # -> denied
    r3 = access_store.create_request(space_id="s3", space_title="C", requester_email="carol@x")
    access_store.decide(r3.id, approve=True, approver_email="admin@x")   # -> approved
    access_store.mark_applied(r3.id, actor_email="admin@x", pr_number=5, pr_url="https://gh/pr/5")

    body = client.get("/api/admin/access-requests", headers=ADMIN_HEADERS).json()
    states = {r["id"]: r["state"] for r in body["requests"]}
    assert states == {r1.id: "requested", r2.id: "denied", r3.id: "applied"}


def test_admin_access_request_queue_requires_the_access_store(monkeypatch, store):
    _setup_admin(monkeypatch)
    engine_api.app.state.access_store = None
    assert client.get("/api/admin/access-requests", headers=ADMIN_HEADERS).status_code == 503


# --- /api/admin/audit: cross-Promotion, newest first, acting identity -----------------------------


def test_admin_audit_aggregates_across_promotions_newest_first_with_actor(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    pid1 = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers, title="Recebíveis")
    pid2 = _promote(monkeypatch, space_id="s2", number=2, requester_headers=ana_headers, title="Merchant")
    # A GitHub-attributed event on promotion 1, occurring AFTER both `requested` events (append-only,
    # so its occurred_at is later than anything recorded above).
    store.append_audit_event(pid1, "pr_review_approved", actor_github_login="PSPedro176",
                             detail={"pr_number": 1})

    body = client.get("/api/admin/audit", headers=ADMIN_HEADERS).json()
    rows = body["audit"]
    assert len(rows) == 3  # 2x requested + 1x pr_review_approved
    # newest first: the just-appended review-approval event leads.
    assert rows[0]["event_type"] == "pr_review_approved"
    assert rows[0]["actor_github_login"] == "PSPedro176"
    assert rows[0]["promotion_id"] == pid1 and rows[0]["resource_id"] == "s1"
    # the two `requested` events for the two promotions both appear, each tagged with its own promotion
    remaining_promo_ids = {r["promotion_id"] for r in rows[1:]}
    assert remaining_promo_ids == {pid1, pid2}
    for r in rows[1:]:
        assert r["event_type"] == "requested"
        assert r["actor_app_email"] == "ana@x"  # display-only OBO attribution, carried through


def test_admin_audit_respects_limit_and_keeps_the_newest(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    pid = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers)
    for i in range(3):
        store.append_audit_event(pid, "re_reviewed", detail={"i": i})

    body = client.get("/api/admin/audit?limit=2", headers=ADMIN_HEADERS).json()
    assert len(body["audit"]) == 2
    # newest first -> the two most recent re_reviewed events, not the oldest `requested` one.
    assert all(r["event_type"] == "re_reviewed" for r in body["audit"])


def test_admin_audit_requires_the_store(monkeypatch):
    _setup_admin(monkeypatch)
    engine_api.app.state.store = None
    assert client.get("/api/admin/audit", headers=ADMIN_HEADERS).status_code == 503


# --- S4 (app-ux-overhaul, GR4): offset pagination + space/actor/date-range filters ----------------


def test_admin_audit_offset_pages_past_the_limit(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    pid = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers)
    for i in range(3):
        store.append_audit_event(pid, "re_reviewed", detail={"i": i})
    # 4 events total (1 requested + 3 re_reviewed), newest first.
    page1 = client.get("/api/admin/audit?limit=2&offset=0", headers=ADMIN_HEADERS).json()["audit"]
    page2 = client.get("/api/admin/audit?limit=2&offset=2", headers=ADMIN_HEADERS).json()["audit"]
    assert len(page1) == 2 and len(page2) == 2
    assert {r["seq"] for r in page1}.isdisjoint({r["seq"] for r in page2})  # no overlap, no gap
    assert page1[-1]["occurred_at"] >= page2[0]["occurred_at"]  # page1 is strictly newer


def test_admin_audit_filters_by_resource_id(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers, title="Recebíveis")
    _promote(monkeypatch, space_id="s2", number=2, requester_headers=ana_headers, title="Merchant")
    body = client.get("/api/admin/audit?resource_id=s1", headers=ADMIN_HEADERS).json()
    assert len(body["audit"]) == 1
    assert body["audit"][0]["resource_id"] == "s1"


def test_admin_audit_filters_by_actor_matching_either_identity_field(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    pid1 = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers)
    _promote(monkeypatch, space_id="s2", number=2, requester_headers=ana_headers)
    store.append_audit_event(pid1, "pr_review_approved", actor_github_login="PSPedro176", detail={})

    by_github = client.get("/api/admin/audit?actor=PSPedro176", headers=ADMIN_HEADERS).json()["audit"]
    assert len(by_github) == 1 and by_github[0]["actor_github_login"] == "PSPedro176"

    by_app_email = client.get("/api/admin/audit?actor=ana@x", headers=ADMIN_HEADERS).json()["audit"]
    assert len(by_app_email) == 2  # both `requested` events attributed to ana's OBO email
    assert all(r["actor_app_email"] == "ana@x" for r in by_app_email)


def test_admin_audit_date_range_filters(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    ana_headers = {"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "ana@x"}
    pid = _promote(monkeypatch, space_id="s1", number=1, requester_headers=ana_headers)
    all_rows = client.get("/api/admin/audit", headers=ADMIN_HEADERS).json()["audit"]
    occurred_at = all_rows[0]["occurred_at"]

    # `after` a point strictly past every event -> empty. Passed via `params=` (not f-string
    # interpolation) so the `+` in the ISO timezone offset gets percent-encoded, not misread as
    # a space in the query string.
    from datetime import datetime, timedelta
    future = (datetime.fromisoformat(occurred_at) + timedelta(days=1)).isoformat()
    r = client.get("/api/admin/audit", params={"after": future}, headers=ADMIN_HEADERS)
    assert r.json()["audit"] == []

    # `before` a point strictly past every event -> everything.
    body = client.get("/api/admin/audit", params={"before": future}, headers=ADMIN_HEADERS).json()
    assert len(body["audit"]) == 1
    assert body["audit"][0]["promotion_id"] == pid


def test_admin_audit_rejects_an_invalid_date(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    r = client.get("/api/admin/audit?after=not-a-date", headers=ADMIN_HEADERS)
    assert r.status_code == 400


# --- /api/admin/rehydrate-events: "Exportações para dev", newest first ----------------------------


def test_admin_rehydrate_events_lists_newest_first(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    store.append_rehydrate_event(resource_id="s1", resource_title="Recebíveis", actor_email="ana@x",
                                 mode="create", dev_space_id="dev-1", detail={"a": 1})
    store.append_rehydrate_event(resource_id="s2", resource_title="Merchant", actor_email="bob@x",
                                 mode="overwrite", dev_space_id="dev-2", detail={"a": 2})

    body = client.get("/api/admin/rehydrate-events", headers=ADMIN_HEADERS).json()
    rows = body["events"]
    assert len(rows) == 2
    assert rows[0]["resource_id"] == "s2" and rows[0]["mode"] == "overwrite"  # newest first
    assert rows[0]["dev_space_id"] == "dev-2" and rows[0]["actor_email"] == "bob@x"
    assert rows[1]["resource_id"] == "s1" and rows[1]["mode"] == "create"


def test_admin_rehydrate_events_empty_when_none_recorded(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    assert client.get("/api/admin/rehydrate-events", headers=ADMIN_HEADERS).json()["events"] == []


def test_admin_rehydrate_events_respects_limit(monkeypatch, store, access_store):
    _setup_admin(monkeypatch)
    for i in range(3):
        store.append_rehydrate_event(resource_id=f"s{i}", resource_title=None, actor_email="ana@x",
                                     mode="create", dev_space_id=f"dev-{i}", detail=None)
    body = client.get("/api/admin/rehydrate-events?limit=2", headers=ADMIN_HEADERS).json()
    assert len(body["events"]) == 2


def test_admin_rehydrate_events_requires_the_store(monkeypatch):
    _setup_admin(monkeypatch)
    engine_api.app.state.store = None
    assert client.get("/api/admin/rehydrate-events", headers=ADMIN_HEADERS).status_code == 503

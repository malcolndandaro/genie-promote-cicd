"""Unit tests for the F3 self-service access-request endpoints (engine_api/main.py). FastAPI
TestClient + a mocked engine/store — no network, no live workspace.

Mirrors tests/test_engine_api.py's conventions: `_verify_as` fakes `authz.verify_identity` so the
requester/approver identity is a platform-VERIFIED value under test control, never trusting a
display header. Verifies: (1) request persists + audits with the verified requester identity;
(2) approver authority is explicit (only a configured admin/steward may decide AT ALL — a random
authenticated user gets 403); (3) SoD — the store's own self-approval guard is server-enforced
(403), not bypassable via a spoofed header; (4) approval applies the grant via the GOVERNED path
(asserted against a FAKE GitHub client — never a live grant/permission call); (5) deny path leaves
the request terminal with no PR; (6) requester status read + audit contents (acting identity).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine_api"))
import authz  # noqa: E402
import main as engine_api  # noqa: E402
import pytest  # noqa: E402
from access_request_store import AccessRequestStore, InMemoryBackend  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(engine_api.app)


def _verify_as(monkeypatch, token_to_email: dict[str, str]):
    def fake_verify(token, *, host=None):
        if token in token_to_email:
            return authz.VerifiedIdentity(user_name=token_to_email[token], group_names=frozenset())
        raise authz.AccessDenied(f"no such token: {token}")

    monkeypatch.setattr(engine_api.authz, "verify_identity", fake_verify)


@pytest.fixture()
def access_store():
    s = AccessRequestStore(InMemoryBackend())
    engine_api.app.state.access_store = s
    yield s
    engine_api.app.state.access_store = None


class _FakeGitHubForAccess:
    """Records what would have been committed; asserts the endpoint never reaches for a live
    grant/permission API (that's checked separately by patching app_logic._client to explode)."""

    def __init__(self):
        self.opened = None

    def get_file_content(self, path, ref=None):
        return None

    def open_or_update_promotion(self, **kw):
        self.opened = kw
        return {"number": 99, "html_url": "https://github.com/o/r/pull/99"}


@pytest.fixture()
def fake_github(monkeypatch):
    gh = _FakeGitHubForAccess()
    monkeypatch.setattr(engine_api.app_logic, "_github_app", lambda *a, **k: gh)
    return gh


# --- request creation --------------------------------------------------------


def test_create_access_request_requires_a_token(access_store):
    r = client.post("/api/access-requests", json={"space_id": "s1"})
    assert r.status_code == 401


def test_create_access_request_persists_with_verified_identity_and_audits(monkeypatch, access_store):
    _verify_as(monkeypatch, {"tok-ana": "ana@x"})
    r = client.post(
        "/api/access-requests",
        json={"space_id": "s1", "space_title": "Recebíveis", "note": "preciso p/ fechamento",
              "want_space_permission": True, "space_permission_level": "CAN_RUN",
              "want_uc_select": True},
        headers={"x-forwarded-access-token": "tok-ana", "x-forwarded-email": "spoofed@evil.com"})
    assert r.status_code == 200
    body = r.json()["request"]
    # requester_email is the VERIFIED identity — never the spoofed display header.
    assert body["requester_email"] == "ana@x"
    assert body["state"] == "requested"
    events = access_store.list_audit_events(body["id"])
    assert [e.event_type for e in events] == ["requested"]
    assert events[0].actor_email == "ana@x"


def test_create_access_request_503_without_store(monkeypatch):
    engine_api.app.state.access_store = None
    _verify_as(monkeypatch, {"tok-ana": "ana@x"})
    r = client.post("/api/access-requests", json={"space_id": "s1"},
                    headers={"x-forwarded-access-token": "tok-ana"})
    assert r.status_code == 503


# --- requester status view + approver pending queue --------------------------


def test_requester_sees_only_their_own_requests(monkeypatch, access_store):
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-bob": "bob@x"})
    client.post("/api/access-requests", json={"space_id": "s1"},
               headers={"x-forwarded-access-token": "tok-ana"})
    client.post("/api/access-requests", json={"space_id": "s2"},
               headers={"x-forwarded-access-token": "tok-bob"})
    mine = client.get("/api/access-requests?scope=mine",
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["requests"]
    assert {r["space_id"] for r in mine} == {"s1"}


def test_pending_queue_is_role_gated_to_admins(monkeypatch, access_store):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-admin": "admin@x"})
    client.post("/api/access-requests", json={"space_id": "s1"},
               headers={"x-forwarded-access-token": "tok-ana"})
    # A non-admin cannot see the approval queue, even by spoofing the display header.
    denied = client.get("/api/access-requests?scope=pending",
                        headers={"x-forwarded-access-token": "tok-ana",
                                 "x-forwarded-email": "admin@x"})
    assert denied.status_code == 403
    ok = client.get("/api/access-requests?scope=pending",
                    headers={"x-forwarded-access-token": "tok-admin"})
    assert ok.status_code == 200
    assert {r["space_id"] for r in ok.json()["requests"]} == {"s1"}


def test_scope_invalid_is_400(access_store):
    assert client.get("/api/access-requests?scope=bogus").status_code == 400


def test_get_access_request_owner_or_admin_only(monkeypatch, access_store):
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-admin": "admin@x", "tok-mallory": "mallory@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    assert client.get(f"/api/access-requests/{rid}",
                      headers={"x-forwarded-access-token": "tok-ana"}).status_code == 200
    assert client.get(f"/api/access-requests/{rid}",
                      headers={"x-forwarded-access-token": "tok-admin"}).status_code == 200
    assert client.get(f"/api/access-requests/{rid}",
                      headers={"x-forwarded-access-token": "tok-mallory"}).status_code == 403


def test_get_access_request_404_for_unknown(access_store):
    assert client.get("/api/access-requests/nope").status_code == 404


# --- decision: approver authority + SoD + governed apply ---------------------


def test_decide_requires_a_token(access_store):
    assert client.post("/api/access-requests/x/decide", json={"approve": True}).status_code == 401


def test_decide_requires_admin_authority(monkeypatch, access_store):
    # Approver authority is EXPLICIT: even a fully-verified, non-admin, non-requester caller may
    # not decide at all — approval authority is the configured admin/steward set, not "anyone but
    # the requester".
    monkeypatch.setenv("APP_ADMINS", "admin@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-rando": "rando@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-rando"})
    assert r.status_code == 403
    assert access_store.get_request(rid).state == "requested"  # untouched


def test_self_approval_is_rejected_even_for_an_admin(monkeypatch, access_store):
    # SoD: an admin who is ALSO the requester cannot approve their own request — enforced
    # server-side (the store's own guard), not just hidden in the UI.
    monkeypatch.setenv("APP_ADMINS", "ana@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-ana"})
    assert r.status_code == 403
    assert access_store.get_request(rid).state == "requested"


def test_a_dedicated_approver_not_admin_can_see_the_pending_queue_and_decide(monkeypatch, access_store):
    # S1/S5 (app-ux-overhaul): Approver is its own persona now — a caller who holds ONLY the
    # Approver role (no APP_ADMINS) can see `scope=pending` and decide, without needing the
    # broader admin surface.
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.setenv("APP_APPROVERS", "approver@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-approver": "approver@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]

    pending = client.get("/api/access-requests?scope=pending",
                         headers={"x-forwarded-access-token": "tok-approver"})
    assert pending.status_code == 200
    assert [r["id"] for r in pending.json()["requests"]] == [rid]

    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": False, "note": "fora do escopo"},
                    headers={"x-forwarded-access-token": "tok-approver"})
    assert r.status_code == 200
    assert r.json()["request"]["state"] == "denied"


def test_a_plain_requester_without_the_approver_role_cannot_see_pending_or_decide(monkeypatch, access_store):
    monkeypatch.delenv("APP_ADMINS", raising=False)
    monkeypatch.delenv("APP_APPROVERS", raising=False)
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-rando": "rando@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]

    assert client.get("/api/access-requests?scope=pending",
                      headers={"x-forwarded-access-token": "tok-rando"}).status_code == 403
    assert client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                       headers={"x-forwarded-access-token": "tok-rando"}).status_code == 403


def test_self_approval_rejected_despite_spoofed_display_header(monkeypatch, access_store):
    monkeypatch.setenv("APP_ADMINS", "ana@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-ana",
                             "x-forwarded-email": "pedro@x"})  # spoofed — ignored for authz
    assert r.status_code == 403


def test_distinct_approver_can_deny(monkeypatch, access_store):
    monkeypatch.setenv("APP_ADMINS", "pedro@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-pedro": "pedro@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    r = client.post(f"/api/access-requests/{rid}/decide",
                    json={"approve": False, "note": "sem justificativa suficiente"},
                    headers={"x-forwarded-access-token": "tok-pedro"})
    assert r.status_code == 200
    body = r.json()["request"]
    assert body["state"] == "denied" and body["decided_by"] == "pedro@x"
    events = access_store.list_audit_events(rid)
    assert [e.event_type for e in events] == ["requested", "denied"]
    assert events[1].actor_email == "pedro@x"


def test_deny_without_a_note_is_rejected_with_400(monkeypatch, access_store):
    # S5 (app-ux-overhaul, D8): denial requires a reason — server-enforced (400), not just a UI
    # affordance. Approving still needs no reason.
    monkeypatch.setenv("APP_ADMINS", "pedro@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-pedro": "pedro@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": False},
                    headers={"x-forwarded-access-token": "tok-pedro"})
    assert r.status_code == 400
    # The request is still `requested` — the rejected decision must not have landed partially.
    status = client.get(f"/api/access-requests/{rid}", headers={"x-forwarded-access-token": "tok-ana"}).json()
    assert status["request"]["state"] == "requested"


def test_approval_applies_the_grant_via_the_governed_path_never_a_direct_mutation(
    monkeypatch, access_store, fake_github
):
    """The heart of F3's acceptance criteria: approval must go through F2's sidecar->PR path
    (asserted against the fake GitHub client) and must NEVER call w.grants/w.permissions."""
    def _boom(*a, **k):
        raise AssertionError("app-direct grant/permission mutation — must be governed")

    from types import SimpleNamespace as NS
    monkeypatch.setattr(engine_api.app_logic, "_client",
                        lambda *a, **k: NS(grants=NS(update=_boom), permissions=NS(update=_boom)))
    monkeypatch.setenv("APP_ADMINS", "pedro@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-pedro": "pedro@x"})
    rid = client.post(
        "/api/access-requests",
        json={"space_id": "s1", "space_title": "Recebíveis", "want_space_permission": True,
              "space_permission_level": "CAN_RUN", "want_uc_select": True},
        headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]

    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-pedro"})
    assert r.status_code == 200
    body = r.json()
    assert body["request"]["state"] == "applied"
    assert body["request"]["pr_number"] == 99
    assert body["pr"] == {"number": 99, "url": "https://github.com/o/r/pull/99"}

    # It committed the requester into the sidecar via the bot PR — NOT a live grant call.
    assert fake_github.opened is not None
    assert fake_github.opened["path"] == "src/genie/s1.access.json"
    assert "ana@x" in fake_github.opened["content"]
    assert fake_github.opened["branch"] == "access/s1"

    events = access_store.list_audit_events(rid)
    assert [e.event_type for e in events] == ["requested", "approved", "applied"]
    assert events[1].actor_email == "pedro@x" and events[2].actor_email == "pedro@x"
    assert events[2].detail["pr_number"] == 99


def test_apply_failure_after_approval_is_recorded_and_surfaced(monkeypatch, access_store):
    """A GitHub failure AFTER the decision is recorded must not look like a silent success: the
    request stays 'approved' (not 'applied'), an apply_failed event captures why, and the HTTP
    call itself reports the failure."""
    class _Boom:
        def get_file_content(self, path, ref=None):
            return None

        def open_or_update_promotion(self, **kw):
            raise RuntimeError("GitHub is down")

    monkeypatch.setattr(engine_api.app_logic, "_github_app", lambda *a, **k: _Boom())
    monkeypatch.setenv("APP_ADMINS", "pedro@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-pedro": "pedro@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]

    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-pedro"})
    assert r.status_code == 502  # _engine_call's generic mapping for an unrecognized engine error

    got = access_store.get_request(rid)
    assert got.state == "approved"  # NOT applied — the grant did not actually land
    events = access_store.list_audit_events(rid)
    assert [e.event_type for e in events] == ["requested", "approved", "apply_failed"]
    assert events[2].actor_email == "pedro@x"


def test_decide_missing_request_404s(monkeypatch, access_store):
    monkeypatch.setenv("APP_ADMINS", "pedro@x")
    _verify_as(monkeypatch, {"tok-pedro": "pedro@x"})
    r = client.post("/api/access-requests/nope/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-pedro"})
    assert r.status_code == 404


def test_decide_twice_is_rejected(monkeypatch, access_store, fake_github):
    monkeypatch.setenv("APP_ADMINS", "pedro@x")
    _verify_as(monkeypatch, {"tok-ana": "ana@x", "tok-pedro": "pedro@x"})
    rid = client.post("/api/access-requests", json={"space_id": "s1"},
                      headers={"x-forwarded-access-token": "tok-ana"}).json()["request"]["id"]
    client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
               headers={"x-forwarded-access-token": "tok-pedro"})
    r = client.post(f"/api/access-requests/{rid}/decide", json={"approve": True},
                    headers={"x-forwarded-access-token": "tok-pedro"})
    assert r.status_code == 409

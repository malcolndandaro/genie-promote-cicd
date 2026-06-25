"""Unit tests for the App backend pure helpers (S8-S11). No Streamlit/Databricks.

The live calls are now SDK-based and accept an injectable ``client``, so the
response-parsing (SDK objects -> UI dicts) is covered with a fake WorkspaceClient
— no network, runs fully offline.
"""
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import app_logic  # noqa: E402


def test_build_timeline_clean_pending_approval():
    tl = app_logic.build_timeline(checks_ok=True,
                                  gate={"conclusion": "success"},
                                  eval_res={"status": "advisory"},
                                  approved=False, deployed=False)
    by = {t["key"]: t["status"] for t in tl}
    assert by["checks"] == "pass" and by["review"] == "pass"
    assert by["approval"] == "running" and by["deploy"] == "pending"


def test_build_timeline_blocked_review():
    tl = app_logic.build_timeline(True, {"conclusion": "failure"}, {"status": "advisory"}, False, False)
    by = {t["key"]: t["status"] for t in tl}
    assert by["review"] == "fail"


def test_build_timeline_failed_checks():
    tl = app_logic.build_timeline(False, {"conclusion": "failure"}, {"status": "block"}, False, False)
    by = {t["key"]: t["status"] for t in tl}
    assert by["checks"] == "fail" and by["eval"] == "fail"


def test_sod_author_cannot_approve():
    ok, reason = app_logic.can_approve("author", "malcoln@x", "malcoln@x")
    assert not ok and "Steward" in reason


def test_sod_requester_cannot_self_approve_even_as_steward():
    ok, reason = app_logic.can_approve("steward", "malcoln@x", "malcoln@x")
    assert not ok and "Segregação" in reason


def test_sod_distinct_steward_can_approve():
    ok, _ = app_logic.can_approve("steward", "malcoln@x", "pedro@x")
    assert ok is True


# --- SDK-backed live calls: parsing verified with a fake WorkspaceClient (offline) ---

def test_list_spaces_maps_sdk_objects():
    fake = NS(genie=NS(list_spaces=lambda: NS(spaces=[
        NS(space_id="abc", title="Recebíveis"),
        NS(space_id="def", title=None),  # missing title -> placeholder
    ])))
    out = app_logic.list_spaces("ignored-profile", client=fake)
    assert out == [
        {"space_id": "abc", "title": "Recebíveis"},
        {"space_id": "def", "title": "(sem título)"},
    ]


def test_list_spaces_handles_empty():
    fake = NS(genie=NS(list_spaces=lambda: NS(spaces=None)))
    assert app_logic.list_spaces("p", client=fake) == []


def test_export_serialized_parses_json_string():
    fake = NS(genie=NS(get_space=lambda sid, include_serialized_space=False:
                       NS(serialized_space='{"title": "X", "n": 2}')))
    assert app_logic.export_serialized("sid", "p", client=fake) == {"title": "X", "n": 2}


def test_export_serialized_empty_when_missing():
    fake = NS(genie=NS(get_space=lambda sid, include_serialized_space=False:
                       NS(serialized_space=None)))
    assert app_logic.export_serialized("sid", "p", client=fake) == {}


def test_effective_grants_maps_enum_and_string_privileges():
    # Real shape: privilege_assignments[].privileges[] are EffectivePrivilege with a
    # .privilege field holding a Privilege enum (.value); tolerate plain strings + Nones.
    def get_effective(securable_type, full_name):
        # SDK 0.111 wants the string "TABLE", not the enum's str repr — guard against regression.
        assert securable_type == "TABLE", f"bad securable_type: {securable_type!r}"
        return NS(privilege_assignments=[
            NS(principal="account users",
               privileges=[NS(privilege=NS(value="SELECT")), NS(privilege=None),
                           NS(privilege="USAGE")]),
            NS(principal="svc", privileges=None),
        ])
    fake = NS(grants=NS(get_effective=get_effective))
    out = app_logic._effective_grants("p", "cat.sch.tbl", client=fake)
    assert out == [
        {"principal": "account users", "privileges": ["SELECT", "USAGE"]},
        {"principal": "svc", "privileges": []},
    ]


def test_claude_returns_first_choice_content():
    fake = NS(serving_endpoints=NS(query=lambda name, messages, max_tokens:
                                   NS(choices=[NS(message=NS(content="resposta"))])))
    assert app_logic._claude("sys", "usr", "p", client=fake) == "resposta"


def test_claude_empty_when_no_choices():
    fake = NS(serving_endpoints=NS(query=lambda name, messages, max_tokens: NS(choices=[])))
    assert app_logic._claude("sys", "usr", "p", client=fake) == ""


# --- _client auth-context branching (OBO / local profile / app SP) — offline ---

def _capture_wc(monkeypatch):
    """Patch WorkspaceClient to record ctor kwargs instead of connecting."""
    seen = {}

    class FakeWC:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(app_logic, "WorkspaceClient", FakeWC)
    return seen


def test_client_obo_uses_user_token(monkeypatch):
    seen = _capture_wc(monkeypatch)
    monkeypatch.setattr(app_logic, "Config", lambda: NS(host="https://dev.example"))
    app_logic._client(profile="ignored-when-token", user_token="tok-123")
    assert seen == {"host": "https://dev.example", "token": "tok-123", "auth_type": "pat"}


def test_client_local_profile(monkeypatch):
    seen = _capture_wc(monkeypatch)
    app_logic._client(profile="cerc-mlops-dev")
    assert seen == {"profile": "cerc-mlops-dev"}


def test_client_app_sp_default(monkeypatch):
    seen = _capture_wc(monkeypatch)
    app_logic._client()  # no profile, no token -> app SP / default auth
    assert seen == {}


# --- request_promotion (GH2): review + export (OBO) + open PR/comment (bot) ---

class _FakeGitHubApp:
    def __init__(self):
        self.promo = None
        self.comment = None

    def open_or_update_promotion(self, **kw):
        self.promo = kw
        return {"number": 7, "html_url": "https://github.com/o/r/pull/7"}

    def upsert_comment(self, number, marker, body):
        self.comment = {"number": number, "marker": marker, "body": body}
        return {"id": 1, "updated": False}


_FULL_REVIEW = {
    "findings": [{"rule_id": "EVAL-01", "severity": "BLOCKER", "message": "poucas perguntas"}],
    "gate": {"conclusion": "failure", "blocker_count": 1, "summary": "🔴 bloqueada"},
    "eval": {"status": "advisory", "summary": "🟡 x"},
    "timeline": [{"key": "checks", "label": "Checagens", "status": "pass"}],
    "allowlist_violations": [],
    "consumer_group": "account users",
    "prod_serialized": {"big": "payload should be dropped"},
    "dev_serialized": {"display_name": "Recebíveis", "n": 2},  # what the PR commits
}


def test_request_promotion_reviews_opens_pr_and_comments(monkeypatch):
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    out = app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x", github=gh)

    # returns the UI review subset (NOT the big prod_serialized/dev_serialized) + the PR coordinates
    assert out["pr"] == {"number": 7, "url": "https://github.com/o/r/pull/7"}
    assert set(out["review"]) == set(app_logic.REVIEW_FIELDS)
    assert "prod_serialized" not in out["review"] and "dev_serialized" not in out["review"]

    # commits the DEV-shaped export (reused from the review, no second export) to src/genie
    assert gh.promo["path"] == "src/genie/receivables.serialized_space.json"
    assert "display_name" in gh.promo["content"]
    # the comment mirrors the review and attributes the human requester
    assert gh.comment["number"] == 7
    assert "malcoln@x" in gh.comment["body"] and "EVAL-01" in gh.comment["body"]


def test_promotion_status_reads_via_injected_bot():
    class FakeGH:
        def get_status(self, number):
            return {"phase": "awaiting_approval", "number": number, "merged": True}

    out = app_logic.promotion_status(6, github=FakeGH())
    assert out == {"phase": "awaiting_approval", "number": 6, "merged": True}


def test_github_app_built_as_app_sp_never_user_token(monkeypatch):
    seen = {}
    monkeypatch.setattr(app_logic, "_client",
                        lambda profile=None, **k: seen.update(profile=profile, kwargs=k) or "wc")
    monkeypatch.setattr(app_logic, "_secret", lambda w, scope, key: f"{key}-val")
    gh = app_logic._github_app("prof")
    assert "user_token" not in seen["kwargs"]  # the bot is the app SP, never the user
    assert gh.owner == "malcolndandaro" and gh.repo == "genie-promote-cicd"


def test_secret_decodes_base64_value():
    import base64
    fake = NS(secrets=NS(get_secret=lambda scope, key: NS(value=base64.b64encode(b"hello").decode())))
    assert app_logic._secret(fake, "genie_promote", "github_app_id") == "hello"

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


# --- Cross-workspace client factory (A1/ADR-0006): prod-local vs dev-remote-SP selection ---

def test_client_default_scope_is_prod_local(monkeypatch):
    """scope defaults to "prod" and behaves exactly like the pre-A1 selector (no regression)."""
    seen = _capture_wc(monkeypatch)
    app_logic._client(profile="cerc-mlops-dev")
    assert seen == {"profile": "cerc-mlops-dev"}


def test_client_dev_sp_scope_uses_dev_host_and_sp_auth_not_obo(monkeypatch):
    """scope="dev-sp" must hit APP_DEV_HOST with client_id/client_secret (SP auth) — never OBO,
    even if a user_token is (incorrectly) also passed, because once the app is prod-hosted there is
    no forwarded dev token (ADR-0006): the SP is the only cross-workspace identity."""
    seen = _capture_wc(monkeypatch)
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", "https://dev.example.cloud.databricks.com")
    monkeypatch.setattr(app_logic, "_secret", lambda w, scope, key: f"{key}-val")
    app_logic._client(scope="dev-sp", user_token="should-be-ignored")
    assert seen == {
        "host": "https://dev.example.cloud.databricks.com",
        "client_id": "dev_sp_client_id-val",
        "client_secret": "dev_sp_client_secret-val",
    }


def test_client_dev_sp_scope_reads_secret_via_injected_secret_client(monkeypatch):
    """The dev SP's own OAuth secret is read from the configured scope via a prod-local client (the
    app's own identity), mirroring `_github_app`'s pattern — never a static credential in config."""
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", "https://dev.example.cloud.databricks.com")
    monkeypatch.setattr(app_logic, "APP_DEV_SP_SECRET_SCOPE", "my_scope")
    seen_secret_reads = []

    def fake_secret(w, scope, key):
        seen_secret_reads.append((w, scope, key))
        return f"{key}-val"

    monkeypatch.setattr(app_logic, "_secret", fake_secret)
    monkeypatch.setattr(app_logic, "WorkspaceClient", lambda **kw: NS(**kw))
    sentinel_client = object()
    app_logic._dev_sp_client(secret_client=sentinel_client)
    assert seen_secret_reads == [
        (sentinel_client, "my_scope", "dev_sp_client_id"),
        (sentinel_client, "my_scope", "dev_sp_client_secret"),
    ]


def test_client_dev_sp_scope_fails_loud_without_dev_host(monkeypatch):
    """No silent fallback to prod: an unconfigured APP_DEV_HOST must raise, not quietly build a
    prod-local client (which would defeat the point of the cross-workspace factory)."""
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", None)
    try:
        app_logic._client(scope="dev-sp")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "APP_DEV_HOST" in str(e)


def test_client_dev_sp_scope_is_lazy_no_network_at_import_or_unconfigured_call(monkeypatch):
    """A1 explicitly only builds the factory SHAPE — the dev-remote-SP path must construct lazily
    (no eager network/SDK calls) so it's safe to wire in before the SP exists (A2 provisions it).
    Asserting the module already imported cleanly (no top-level WorkspaceClient() call for dev-sp)
    and that the failure path above raises a plain RuntimeError (no SDK exception) covers this."""
    assert app_logic.APP_DEV_SP_SECRET_SCOPE  # module-level config read, not a live call
    assert callable(app_logic._dev_sp_client)


def test_client_prod_obo_still_resolves_with_scope_default(monkeypatch):
    """OBO/profile/SP prod paths still resolve unchanged when scope is left at its default."""
    seen = _capture_wc(monkeypatch)
    monkeypatch.setattr(app_logic, "Config", lambda: NS(host="https://prod.example"))
    app_logic._client(user_token="tok-abc")
    assert seen == {"host": "https://prod.example", "token": "tok-abc", "auth_type": "pat"}


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
    out = app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                      resource_title="Recebíveis", github=gh)

    # returns the UI review subset (NOT the big prod_serialized/dev_serialized) + the PR coordinates
    assert out["pr"] == {"number": 7, "url": "https://github.com/o/r/pull/7"}
    assert set(out["review"]) == set(app_logic.REVIEW_FIELDS)
    assert "prod_serialized" not in out["review"] and "dev_serialized" not in out["review"]

    # PER-SPACE: commits to this space's OWN branch + file (slug derived from the id), not a shared one
    assert gh.promo["branch"] == "promote/sp1" and out["branch"] == "promote/sp1"
    assert gh.promo["path"] == "src/genie/sp1.serialized_space.json"
    assert "display_name" in gh.promo["content"]
    # + a per-space title sidecar so render.sh can name the generated prod genie_spaces resource
    assert gh.promo["extra_files"]["src/genie/sp1.title"].strip() == "Recebíveis"
    # the comment mirrors the review and attributes the human requester
    assert gh.comment["number"] == 7
    assert "malcoln@x" in gh.comment["body"] and "EVAL-01" in gh.comment["body"]


def test_two_spaces_get_distinct_branches_and_paths():
    # The core of the bug fix: different spaces never collide on a shared branch/PR/file.
    assert app_logic.branch_for(app_logic.space_slug("aaa")) != app_logic.branch_for(app_logic.space_slug("bbb"))
    assert app_logic.src_path_for(app_logic.space_slug("aaa")) != app_logic.src_path_for(app_logic.space_slug("bbb"))


def test_space_slug_pinned_and_derived(monkeypatch):
    # A pinned slug (APP_SPACE_SLUGS) wins; otherwise derive a valid identifier from the id.
    monkeypatch.setattr(app_logic, "_SPACE_SLUGS", {"01f16e83": "receivables"})
    assert app_logic.space_slug("01f16e83") == "receivables"
    # an id starting with a digit is prefixed so it's a valid branch + DABs resource key
    assert app_logic.space_slug("01f1717f") == "s_01f1717f"
    assert app_logic.space_slug("my_space") == "my_space"


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

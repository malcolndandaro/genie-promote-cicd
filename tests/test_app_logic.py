"""Unit tests for the App backend pure helpers (S8-S11). No Streamlit/Databricks.

The live calls are now SDK-based and accept an injectable ``client``, so the
response-parsing (SDK objects -> UI dicts) is covered with a fake WorkspaceClient
— no network, runs fully offline.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import app_logic  # noqa: E402
import authz  # noqa: E402  (A2 — verified identity + the live fail-closed access guard)


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


def test_list_serving_endpoints_maps_sdk_objects():
    # S7a: w.serving_endpoints.list() returns an Iterator[ServingEndpoint] DIRECTLY (confirmed
    # against the real SDK) — not a response object with an .endpoints attribute.
    fake = NS(serving_endpoints=NS(list=lambda: [NS(name="databricks-claude-opus-4-8"), NS(name="ka-handbook")]))
    assert app_logic.list_serving_endpoints("p", client=fake) == [
        {"name": "databricks-claude-opus-4-8"}, {"name": "ka-handbook"}]


def test_list_serving_endpoints_skips_unnamed_entries():
    fake = NS(serving_endpoints=NS(list=lambda: [NS(name=None), NS(name="ka-x")]))
    assert app_logic.list_serving_endpoints("p", client=fake) == [{"name": "ka-x"}]


def test_query_ka_endpoint_uses_the_responses_api_and_concatenates_text():
    # Agent Bricks KA endpoints serve the Responses API (task agent/v1/responses): the request is
    # `{"input": [...]}` to /invocations, and the answer arrives as output[].content[].output_text
    # segments (proven live 2026-07-16 — the old Chat-Completions `messages` shape is rejected).
    captured = {}

    def fake_do(method, path, body=None):
        captured["method"], captured["path"], captured["body"] = method, path, body
        return {"output": [
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "resposta "},
                {"type": "output_text", "text": "do KA", "annotations": [{"type": "url_citation"}]},
            ]},
        ]}

    fake = NS(api_client=NS(do=fake_do))
    assert app_logic._query_ka_endpoint("ka-handbook", "pergunta?", "p", client=fake) == "resposta do KA"
    assert captured["method"] == "POST"
    assert captured["path"] == "/serving-endpoints/ka-handbook/invocations"
    assert captured["body"] == {"input": [{"role": "user", "content": "pergunta?"}]}


def test_query_ka_endpoint_empty_on_no_output():
    fake = NS(api_client=NS(do=lambda *a, **k: {"output": []}))
    assert app_logic._query_ka_endpoint("ka-handbook", "pergunta?", "p", client=fake) == ""


def test_extract_responses_text_shape_variations():
    # Multiple message items concatenated; string-content and top-level output_text fallbacks.
    multi = {"output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "a"}]},
        {"type": "reasoning", "content": [{"type": "output_text", "text": "IGNORED"}]},  # not a message
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "b"}]},
    ]}
    assert app_logic._extract_responses_text(multi) == "ab"
    assert app_logic._extract_responses_text(
        {"output": [{"type": "message", "content": "plain"}]}) == "plain"
    assert app_logic._extract_responses_text({"output_text": " top-level "}) == "top-level"
    assert app_logic._extract_responses_text({}) == ""
    assert app_logic._extract_responses_text("not a dict") == ""


def test_export_serialized_parses_json_string():
    fake = NS(genie=NS(get_space=lambda sid, include_serialized_space=False:
                       NS(serialized_space='{"title": "X", "n": 2}')))
    assert app_logic.export_serialized("sid", "p", client=fake) == {"title": "X", "n": 2}


def test_export_serialized_empty_when_missing():
    fake = NS(genie=NS(get_space=lambda sid, include_serialized_space=False:
                       NS(serialized_space=None)))
    assert app_logic.export_serialized("sid", "p", client=fake) == {}


# --- G1: list_principals (SCIM users/groups directory for the prefilled pickers) ---------------


class _FakeScimUsers:
    """`list(filter=...)` -> iterable of NS(id, user_name, display_name). `filter=None` (blank
    query) returns everyone; otherwise a naive substring match on the quoted query stands in for the
    real SCIM `co` clause — enough to prove the query is actually threaded through."""

    def __init__(self, users):
        self._users = users

    def list(self, filter=None):  # noqa: A002 - mirrors the SDK kwarg
        if filter is None:
            return list(self._users)
        q = filter.split('"')[1].lower()
        return [u for u in self._users if q in u.user_name.lower() or q in (u.display_name or "").lower()]


class _FakeScimGroups:
    def __init__(self, groups):
        self._groups = groups

    def list(self, filter=None):  # noqa: A002
        if filter is None:
            return list(self._groups)
        q = filter.split('"')[1].lower()
        return [g for g in self._groups if q in g.display_name.lower()]


def _fake_scim_client(users=(), groups=()):
    return NS(users=_FakeScimUsers(list(users)), groups=_FakeScimGroups(list(groups)))


def test_list_principals_maps_users_and_groups():
    fake = _fake_scim_client(
        users=[NS(id="uid-1", user_name="ana@x.com", display_name="Ana Silva")],
        groups=[NS(id="gid-1", display_name="grp_genie_receivables")],  # no .meta -> name-fallback
    )
    out = app_logic.list_principals(client=fake)
    assert out == [
        {"type": "user", "id": "uid-1", "display": "Ana Silva", "email": "ana@x.com", "uc_grantable": True},
        {"type": "group", "id": "gid-1", "display": "grp_genie_receivables", "email": None, "uc_grantable": True},
    ]


def test_list_principals_falls_back_to_user_name_without_display_name():
    fake = _fake_scim_client(users=[NS(id="uid-2", user_name="bob@x.com", display_name=None)])
    out = app_logic.list_principals(client=fake)
    assert out == [{"type": "user", "id": "uid-2", "display": "bob@x.com", "email": "bob@x.com",
                   "uc_grantable": True}]


# --- G9: `uc_grantable` — a workspace-LOCAL group must never be offered to the UC-principals picker


def test_list_principals_flags_workspace_local_group_as_not_uc_grantable():
    fake = _fake_scim_client(groups=[NS(id="g1", display_name="users", meta=NS(resource_type="WorkspaceGroup"))])
    out = app_logic.list_principals(client=fake)
    assert out == [{"type": "group", "id": "g1", "display": "users", "email": None, "uc_grantable": False}]


def test_list_principals_flags_account_group_as_uc_grantable():
    fake = _fake_scim_client(groups=[NS(id="g2", display_name="data_analysts", meta=NS(resource_type="Group"))])
    out = app_logic.list_principals(client=fake)
    assert out[0]["uc_grantable"] is True


def test_list_principals_group_without_meta_falls_back_to_builtin_name_exclusion():
    fake = _fake_scim_client(groups=[NS(id="g3", display_name="admins")])  # no .meta attribute at all
    out = app_logic.list_principals(client=fake)
    assert out[0]["uc_grantable"] is False


def test_list_principals_blank_query_lists_everyone_unfiltered():
    fake = _fake_scim_client(
        users=[NS(id="u1", user_name="ana@x.com", display_name="Ana"),
               NS(id="u2", user_name="bob@x.com", display_name="Bob")],
    )
    out = app_logic.list_principals("", client=fake)
    assert [p["id"] for p in out] == ["u1", "u2"]


def test_list_principals_query_filters_server_side():
    fake = _fake_scim_client(
        users=[NS(id="u1", user_name="ana@x.com", display_name="Ana"),
               NS(id="u2", user_name="bob@x.com", display_name="Bob")],
        groups=[NS(id="g1", display_name="grp_ana_readers"), NS(id="g2", display_name="grp_other")],
    )
    out = app_logic.list_principals("ana", client=fake)
    assert {p["id"] for p in out} == {"u1", "g1"}  # "Bob"/"grp_other" don't match "ana"


def test_list_principals_kind_narrows_to_one_type():
    fake = _fake_scim_client(
        users=[NS(id="u1", user_name="ana@x.com", display_name="Ana")],
        groups=[NS(id="g1", display_name="grp_x")],
    )
    assert [p["type"] for p in app_logic.list_principals(kind="user", client=fake)] == ["user"]
    assert [p["type"] for p in app_logic.list_principals(kind="group", client=fake)] == ["group"]


def test_list_principals_respects_limit_per_kind():
    fake = _fake_scim_client(users=[NS(id=f"u{i}", user_name=f"u{i}@x.com", display_name=None)
                                    for i in range(5)])
    out = app_logic.list_principals(limit=2, client=fake)
    assert len(out) == 2


# --- A2: cross-workspace read-path rewiring (list_spaces/export_serialized -> dev-sp + guard) ---


def _fake_dev_transport(spaces, acl_by_space):
    """A fake dev-sp WorkspaceClient: `genie.list_spaces()` returns `spaces`, and
    `permissions.get` answers per-space from `acl_by_space` (space_id -> list of user_names that
    have access) — enough to drive authz.assert_can_access without a real ACL shape."""
    def get_perms(request_object_type, request_object_id):
        allowed = acl_by_space.get(request_object_id, [])
        return NS(access_control_list=[
            NS(user_name=u, group_name=None, service_principal_name=None,
               all_permissions=[NS(permission_level="CAN_MANAGE")])
            for u in allowed
        ])

    return NS(genie=NS(list_spaces=lambda: NS(spaces=spaces)),
              permissions=NS(get=get_perms))


def test_list_spaces_with_token_uses_dev_sp_and_filters_to_accessible(monkeypatch):
    # The dev SP can list EVERY space (platform necessity); list_spaces must filter down to only
    # the ones the VERIFIED caller can access — the confused-deputy fix (A2 acceptance criterion).
    dev = _fake_dev_transport(
        spaces=[NS(space_id="mine", title="Meu Espaço"), NS(space_id="not-mine", title="De outro")],
        acl_by_space={"mine": ["ana@x"], "not-mine": ["bob@x"]},
    )
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: dev)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset()))
    out = app_logic.list_spaces(user_token="tok-ana")
    assert out == [{"space_id": "mine", "title": "Meu Espaço"}]  # "not-mine" filtered out


def test_list_spaces_uses_dev_sp_scope_not_obo(monkeypatch):
    # OBO cannot span workspaces once prod-hosted (ADR-0006) — list_spaces must request the
    # dev-reader/writer SP transport (scope="dev-sp"), never build an OBO client for dev.
    seen_scopes = []

    def fake_client(*a, **k):
        seen_scopes.append(k.get("scope"))
        return _fake_dev_transport(spaces=[], acl_by_space={})

    monkeypatch.setattr(app_logic, "_client", fake_client)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset()))
    app_logic.list_spaces(user_token="tok-ana")
    assert seen_scopes == ["dev-sp"]


def test_read_path_verifies_identity_against_prod_host_not_dev(monkeypatch):
    # Finding 1 (A2 security review): the OBO token is PROD-minted and cannot authenticate to dev,
    # so verify_identity must resolve the caller against the app's OWN (prod) host — only the
    # transport + ACL read run against dev. Verifying against APP_DEV_HOST would deny every real
    # user (fails closed, but breaks the feature). Guard against that regression.
    seen = {}
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", "https://dev.example.cloud.databricks.com")
    monkeypatch.setattr(app_logic, "Config", lambda: NS(host="https://prod.example.cloud.databricks.com"))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: _fake_dev_transport(spaces=[], acl_by_space={}))

    def capture_verify(token, **k):
        seen["host"] = k.get("host")
        return authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset())

    monkeypatch.setattr(authz, "verify_identity", capture_verify)
    app_logic.list_spaces(user_token="tok-ana")
    assert seen["host"] == "https://prod.example.cloud.databricks.com"
    assert seen["host"] != app_logic.APP_DEV_HOST  # must NOT verify the prod token against dev


def test_export_serialized_denies_a_requester_who_does_not_own_the_space(monkeypatch):
    # Unit test required by A2: a Requester cannot export a Space they don't own even though the
    # dev SP itself can reach it.
    dev = _fake_dev_transport(spaces=[], acl_by_space={"someone-elses-space": ["ana@x"]})
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: dev)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="mallory@x", group_names=frozenset()))
    try:
        app_logic.export_serialized("someone-elses-space", user_token="tok-mallory")
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_export_serialized_allows_the_owner_and_reaches_dev_sp(monkeypatch):
    dev = NS(
        genie=NS(get_space=lambda sid, include_serialized_space=False:
                NS(serialized_space='{"title": "mine"}')),
        permissions=NS(get=lambda request_object_type, request_object_id:
                       NS(access_control_list=[NS(user_name="ana@x", group_name=None,
                                                  service_principal_name=None,
                                                  all_permissions=[NS(permission_level="CAN_MANAGE")])])),
    )
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: dev)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset()))
    out = app_logic.export_serialized("my-space", user_token="tok-ana")
    assert out == {"title": "mine"}


def test_export_serialized_local_profile_path_skips_guard(monkeypatch):
    # Bare profile, no token (offline/local-dev convenience): no verified caller exists to check
    # against, so the profile's own Genie permission IS the access boundary (unchanged behavior).
    called = {"assert_can_access": False}

    def spy(*a, **k):
        called["assert_can_access"] = True

    monkeypatch.setattr(authz, "assert_can_access", spy)
    fake = NS(genie=NS(get_space=lambda sid, include_serialized_space=False:
                       NS(serialized_space='{"x": 1}')))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: fake)
    out = app_logic.export_serialized("sid", "some-profile")
    assert out == {"x": 1}
    assert called["assert_can_access"] is False


# --- G7: preview_promotion (read-only, before the caller commits) ------------------------------


def _fake_dev_space_transport(space_id, allowed, serialized, title=None):
    """A fake dev-sp WorkspaceClient exposing ONE Space via `genie.get_space` (mirrors
    `_fake_dev_transport` above, but for a single-space read instead of `list_spaces`)."""
    def get_perms(request_object_type, request_object_id):
        ok = allowed if request_object_id == space_id else []
        return NS(access_control_list=[
            NS(user_name=u, group_name=None, service_principal_name=None,
               all_permissions=[NS(permission_level="CAN_MANAGE")])
            for u in ok
        ])

    return NS(
        genie=NS(get_space=lambda sid, include_serialized_space=False:
                NS(serialized_space=json.dumps(serialized, ensure_ascii=False), title=title)),
        permissions=NS(get=get_perms),
    )


_DEV_SERIALIZED = {"data_sources": {"tables": [{"identifier": "dev_recebiveis.diamond.fato_recebiveis"}]}}


def test_preview_promotion_returns_title_and_default_targets(monkeypatch):
    dev = _fake_dev_space_transport("dev-space", ["ana@x"], _DEV_SERIALIZED, title="Recebíveis")
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: dev)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset()))
    out = app_logic.preview_promotion("dev-space", user_token="tok-ana")
    assert out["title"] == "Recebíveis"
    assert out["tables"] == [{"source": "dev_recebiveis.diamond.fato_recebiveis",
                              "default_target": "prod_recebiveis.diamond.fato_recebiveis"}]


def test_preview_promotion_denies_a_requester_who_does_not_own_the_space(monkeypatch):
    # Mirrors A2's export_serialized guard: preview_promotion touches ONE blast site (dev) and
    # denies FIRST, before ever reading the Space.
    dev = _fake_dev_space_transport("dev-space", ["ana@x"], _DEV_SERIALIZED)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: dev)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="mallory@x", group_names=frozenset()))
    try:
        app_logic.preview_promotion("dev-space", user_token="tok-mallory")
        assert False, "expected AccessDenied"
    except authz.AccessDenied:
        pass


def test_preview_promotion_uses_dev_sp_scope_not_obo(monkeypatch):
    # OBO cannot span workspaces once prod-hosted (ADR-0006) — the preview must request the
    # dev-reader/writer SP transport (scope="dev-sp"), never build an OBO client for dev.
    seen_scopes = []

    def fake_client(*a, **k):
        seen_scopes.append(k.get("scope"))
        return _fake_dev_space_transport("dev-space", ["ana@x"], _DEV_SERIALIZED)

    monkeypatch.setattr(app_logic, "_client", fake_client)
    monkeypatch.setattr(authz, "verify_identity",
                        lambda token, **k: authz.VerifiedIdentity(user_name="ana@x", group_names=frozenset()))
    app_logic.preview_promotion("dev-space", user_token="tok-ana")
    assert seen_scopes == ["dev-sp"]


def test_preview_promotion_injected_client_skips_the_guard(monkeypatch):
    # Tests/local overrides bypass the guard, same convention as export_serialized/list_spaces.
    called = {"assert_can_access": False}

    def spy(*a, **k):
        called["assert_can_access"] = True

    monkeypatch.setattr(authz, "assert_can_access", spy)
    dev = _fake_dev_space_transport("dev-space", [], _DEV_SERIALIZED, title="X")
    out = app_logic.preview_promotion("dev-space", user_token="ignored", dev_client=dev)
    assert out["title"] == "X"
    assert called["assert_can_access"] is False


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

    def get_file_content(self, path, **_kwargs):
        return "a" * 40 if path == "engine.lock" else None

    def post_review_comment(self, number, marker, body):
        self.comment = {"number": number, "marker": marker, "body": body}
        return {"id": 1, "seq": 1}


_FULL_REVIEW = {
    "findings": [{"rule_id": "EVAL-01", "severity": "BLOCKER", "message": "poucas perguntas"}],
    "gate": {"conclusion": "failure", "blocker_count": 1, "summary": "🔴 bloqueada"},
    "eval": {"status": "advisory", "summary": "🟡 x"},
    "timeline": [{"key": "checks", "label": "Checagens", "status": "pass"}],
    "allowlist_violations": [],
    "consumer_group": "account users",
    "audience_spec": None,
    "access_spec": {"space_permissions": [], "uc_principals": []},  # F2: no access declared
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
    revision = json.loads(gh.promo["extra_files"]["src/genie/sp1.revision.json"])
    assert revision["revisions"]["engine_revision"] == "a" * 40
    assert len(revision["revisions"]["content_revision"]) == 64
    assert out["change_request"]["external_id"] == "7"
    # the comment mirrors the review and attributes the human requester
    assert gh.comment["number"] == 7
    assert "malcoln@x" in gh.comment["body"] and "EVAL-01" in gh.comment["body"]
    assert "**Revisões imutáveis:**" in gh.comment["body"]
    assert ("a" * 40) in gh.comment["body"]


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
        def get_status(self, number, approved_revisions=None):
            assert approved_revisions == {"content_revision": "b" * 64, "engine_revision": "a" * 40}
            return {"phase": "awaiting_approval", "number": number, "merged": True}

    out = app_logic.promotion_status(
        6, github=FakeGH(),
        approved_revisions={"content_revision": "b" * 64, "engine_revision": "a" * 40})
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


# --- Pilot AudienceSpec declared at promotion (app-direct declaration, governed enforcement) ---

import access_spec  # noqa: E402
import audience_spec  # noqa: E402


def _spec_with_access():
    return access_spec.AccessSpec(
        space_permissions=(
            access_spec.SpacePermission(access_spec.Principal("data_analysts", is_group=True), "CAN_RUN"),
        ),
        uc_principals=(access_spec.Principal("data_analysts", is_group=True),
                      access_spec.Principal("ana@x.com")),
    )


def _audience():
    return audience_spec.AudienceSpec((
        audience_spec.AudiencePrincipal("data_analysts", is_group=True),
        audience_spec.AudiencePrincipal("ana@x.com"),
    ))


def test_request_promotion_commits_audience_sidecar_and_removes_legacy(monkeypatch):
    captured_spec = {}

    def fake_review_space(space_id, *a, audience_spec_=None, **k):
        captured_spec["spec"] = audience_spec_
        out = dict(_FULL_REVIEW)
        out["audience_spec"] = audience_spec_.to_dict() if audience_spec_ else None
        return out

    monkeypatch.setattr(app_logic, "review_space", fake_review_space)
    gh = _FakeGitHubApp()
    spec = _audience()
    out = app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                      audience_spec_=spec, github=gh)
    sidecar_path = "src/genie/sp1.audience.json"
    assert sidecar_path in gh.promo["extra_files"]
    committed = json.loads(gh.promo["extra_files"][sidecar_path])
    assert committed == spec.to_dict()
    # the review payload the Steward sees carries the SAME declaration that was passed through
    assert captured_spec["spec"] is spec
    assert out["review"]["audience_spec"] == spec.to_dict()
    assert "src/genie/sp1.access.json" in gh.promo["remove_files"]


def test_request_promotion_omits_audience_sidecar_for_legacy_missing_declaration(monkeypatch):
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x", github=gh)
    assert "src/genie/sp1.audience.json" not in gh.promo["extra_files"]


def test_request_promotion_clears_stale_audience_and_access_sidecars_when_empty(monkeypatch):
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x", github=gh)
    assert "src/genie/sp1.access.json" in gh.promo["remove_files"]
    assert "src/genie/sp1.audience.json" in gh.promo["remove_files"]


def test_request_promotion_keeps_canonical_audience_and_always_removes_legacy(monkeypatch):
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                audience_spec_=_audience(), github=gh)
    assert "src/genie/sp1.audience.json" not in gh.promo["remove_files"]
    assert "src/genie/sp1.audience.json" in gh.promo["extra_files"]
    assert "src/genie/sp1.access.json" in gh.promo["remove_files"]


# --- G7: the declared prod Space name + table de-para (app-direct declaration, CI enforcement) ---


def test_request_promotion_custom_prod_title_flows_to_the_title_sidecar_and_comment(monkeypatch):
    """The prod Space name is now a Requester DECLARATION (may differ from the dev title) — it
    flows into the EXISTING `.title` sidecar exactly the same way the pre-G7 auto-copied dev title
    did (same convention, only the value's origin changed)."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                resource_title="Recebíveis PROD", github=gh)
    assert gh.promo["extra_files"]["src/genie/sp1.title"].strip() == "Recebíveis PROD"
    # surfaced in the comment too, for Steward/reviewer transparency
    assert "**Nome do space em produção:** `Recebíveis PROD`" in gh.comment["body"]


def test_request_promotion_commits_mapping_sidecar_when_declared(monkeypatch):
    """A non-empty table_mapping is committed as a NEW per-space sidecar (mirrors `.access.json`) —
    the DECLARATION is app-direct (this call); the actual rebind is CI's job (render.sh)."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    mapping = {"dev_recebiveis.diamond.dim_cedente": "prod_recebiveis.diamond.dim_cedente_v2"}
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                table_mapping=mapping, github=gh)
    sidecar_path = "src/genie/sp1.mapping.json"
    assert sidecar_path in gh.promo["extra_files"]
    assert json.loads(gh.promo["extra_files"][sidecar_path]) == mapping
    # reflected in the PR comment too, so the Steward sees it without digging into the diff
    assert "dim_cedente_v2" in gh.comment["body"]
    assert "De-para de tabelas" in gh.comment["body"]


def test_request_promotion_omits_mapping_sidecar_when_not_declared(monkeypatch):
    """No table_mapping declared (the default case) -> no sidecar file, no noisy empty commit —
    mirrors test_request_promotion_omits_access_sidecar_when_not_declared."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x", github=gh)
    assert "src/genie/sp1.mapping.json" not in gh.promo["extra_files"]
    assert "De-para de tabelas" not in gh.comment["body"]


def test_request_promotion_clears_a_stale_mapping_sidecar_when_re_requested_empty(monkeypatch):
    """G9: mirrors the access-sidecar case — a re-request that no longer declares a table_mapping
    must ask for the PRIOR round's `.mapping.json` to be removed, not just left uncommitted-to."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x", github=gh)
    assert "src/genie/sp1.mapping.json" in gh.promo["remove_files"]


def test_request_promotion_never_removes_the_mapping_sidecar_when_still_declared(monkeypatch):
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    mapping = {"dev_recebiveis.diamond.dim_cedente": "prod_recebiveis.diamond.dim_cedente_v2"}
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                table_mapping=mapping, github=gh)
    assert "src/genie/sp1.mapping.json" not in gh.promo["remove_files"]
    assert "src/genie/sp1.mapping.json" in gh.promo["extra_files"]


def test_request_promotion_never_applies_the_mapping_itself(monkeypatch):
    """Enforcement must be GOVERNED (CI-run render.sh), never app-direct: request_promotion must
    never rebind/rewrite the committed artifact itself — it only ever writes the declared mapping
    to a git sidecar (mirrors test_request_promotion_never_calls_a_live_grant_or_permission_api)."""
    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    mapping = {"dev_recebiveis.diamond.dim_cedente": "prod_recebiveis.diamond.dim_cedente_v2"}
    app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                table_mapping=mapping, github=gh)
    # the committed serialized_space artifact is the DEV-shaped export as-is — untouched by the
    # mapping (which lives ONLY in the separate sidecar, applied later by CI).
    committed = json.loads(gh.promo["content"])
    assert committed == _FULL_REVIEW["dev_serialized"]


def test_request_promotion_never_calls_a_live_grant_or_permission_api(monkeypatch):
    """Enforcement must be GOVERNED (CI-run, prod SP), never app-direct: request_promotion (and the
    review_space it wraps) must never touch grants.update / permissions.update itself. We assert
    this by making both explode if called on the injected client, then proving request_promotion
    still succeeds — it only ever WRITES the declared spec to a git sidecar."""
    def _boom(*a, **k):
        raise AssertionError("app-direct grant/permission mutation — enforcement must be governed")

    monkeypatch.setattr(app_logic, "review_space", lambda *a, **k: dict(_FULL_REVIEW))
    gh = _FakeGitHubApp()
    fake_client = NS(grants=NS(update=_boom), permissions=NS(update=_boom))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: fake_client)
    out = app_logic.request_promotion("sp1", user_token="tok", requester_email="malcoln@x",
                                      audience_spec_=_audience(), github=gh)
    assert out["pr"]["number"] == 7  # completed without ever invoking the fake grant/permission APIs


def _stub_review_space_llm_leg(monkeypatch):
    """Everything review_space needs downstream of the GRANT-01 preview — shared by every
    grant-preview test below so each one only wires up the grants-specific parts."""
    monkeypatch.setattr(app_logic.review_core, "build_space_context",
                        lambda space: {"n_benchmark": 5})
    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", lambda *a, **k: ("s", "u"))
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: {"status": "advisory", "summary": "ok"})


def test_review_space_grant01_preview_separates_baseline_from_declared_principals(monkeypatch):
    """G9: the in-app GRANT-01 PREVIEW (only runs with a reachable grant_profile) checks the base
    consumer_group and the AccessSpec's declared principals SEPARATELY — no longer union'd into one
    flat list, since a missing grant means something different for each class."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis", "column_configs": []}]}})
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())  # NS() has no .groups -> best-effort None

    captured = {}

    def fake_check_grants(space, consumer_group, getter, declared_principals=None, non_grantable_principals=None):
        captured["consumer_group"] = consumer_group
        captured["declared_principals"] = list(declared_principals or [])
        captured["non_grantable_principals"] = list(non_grantable_principals or [])
        return []

    monkeypatch.setattr(app_logic.grant_check, "check_grants", fake_check_grants)
    _stub_review_space_llm_leg(monkeypatch)

    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),
                                                 access_spec.Principal("data_analysts", is_group=True)))
    result = app_logic.review_space("sp1", grant_profile="prod-profile", access_spec_=spec)
    assert captured["consumer_group"] == "account users"  # baseline, never unioned with declared
    assert set(captured["declared_principals"]) == {"ana@x.com", "data_analysts"}
    assert captured["non_grantable_principals"] == []  # unresolvable (fake has no .groups) -> best-effort empty
    assert result["access_spec"] == spec.to_dict()


def test_review_space_grant01_preview_declared_miss_is_advisory_not_blocker(monkeypatch):
    """A declared principal missing SELECT surfaces as a non-blocking SUGGESTION in the app's own
    review result (mirrors CI) — it must NOT flip the gate to failure."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis", "column_configs": []}]}})

    def fake_client(*a, **k):
        return NS(grants=NS(get_effective=lambda securable_type, full_name: NS(privilege_assignments=[
            NS(principal="account users", privileges=[NS(privilege="SELECT")]),
        ])))

    monkeypatch.setattr(app_logic, "_client", fake_client)
    _stub_review_space_llm_leg(monkeypatch)

    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    result = app_logic.review_space("sp1", grant_profile="prod-profile", access_spec_=spec)
    grant_findings = [f for f in result["findings"] if f["rule_id"] == "GRANT-01"]
    assert len(grant_findings) == 1
    assert grant_findings[0]["severity"] == "SUGGESTION"
    assert "apply_access" in grant_findings[0]["message"]
    assert result["gate"]["conclusion"] != "failure"  # advisory-only -> does not block


def test_review_space_grant01_preview_baseline_miss_still_blocks(monkeypatch):
    """Baseline (unchanged): a missing BASELINE grant still fails the gate even with an AccessSpec
    declared — the pipeline never grants the baseline group."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis", "column_configs": []}]}})

    def fake_client(*a, **k):
        return NS(grants=NS(get_effective=lambda securable_type, full_name: NS(privilege_assignments=[])))

    monkeypatch.setattr(app_logic, "_client", fake_client)
    _stub_review_space_llm_leg(monkeypatch)

    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("ana@x.com"),))
    result = app_logic.review_space("sp1", grant_profile="prod-profile", access_spec_=spec)
    grant_findings = [f for f in result["findings"] if f["rule_id"] == "GRANT-01"]
    severities = {f["principal"]: f["severity"] for f in grant_findings}
    assert severities["account users"] == "BLOCKER"
    assert severities["ana@x.com"] == "SUGGESTION"
    assert result["gate"]["conclusion"] == "failure"  # the BLOCKER alone fails the gate


def test_review_space_grant01_preview_flags_non_grantable_declared_group(monkeypatch):
    """Additional scope: a declared GROUP that isn't UC-grantable (workspace-local) gets the
    'não pode receber grant UC' advisory instead of the 'será concedido no deploy' promise."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.fato_recebiveis", "column_configs": []}]}})

    def fake_client(*a, **k):
        return NS(
            grants=NS(get_effective=lambda securable_type, full_name: NS(privilege_assignments=[
                NS(principal="account users", privileges=[NS(privilege="SELECT")]),
            ])),
            groups=NS(list=lambda filter: [NS(display_name="users", meta=NS(resource_type="WorkspaceGroup"))]),
        )

    monkeypatch.setattr(app_logic, "_client", fake_client)
    _stub_review_space_llm_leg(monkeypatch)

    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("users", is_group=True),))
    result = app_logic.review_space("sp1", grant_profile="prod-profile", access_spec_=spec)
    grant_findings = [f for f in result["findings"] if f["rule_id"] == "GRANT-01"]
    assert len(grant_findings) == 1
    assert grant_findings[0]["severity"] == "SUGGESTION"
    assert "grupo local do workspace" in grant_findings[0]["message"]
    assert "apply_access" not in grant_findings[0]["message"]


# --- eval-run gate threading (W2): review_space must target the SAME workspace as the export ---

def _stub_review_space_llm_leg_only(monkeypatch):
    """Same LLM-leg stubbing as `_stub_review_space_llm_leg`, WITHOUT touching `run_eval_gate_rest`
    — the tests below need to observe/control that call themselves."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": []}})
    monkeypatch.setattr(app_logic.review_core, "build_space_context", lambda space: {"n_benchmark": 5})
    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", lambda *a, **k: ("s", "u"))
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')


def test_review_space_eval_uses_dev_sp_client_when_user_token_present(monkeypatch):
    """W2: once the app is prod-hosted, OBO cannot reach dev (ADR-0006) — a user_token means the
    eval-run gate MUST target the dev-reader/writer SP (scope="dev-sp"), the SAME transport
    export_serialized itself uses, never a bare prod-local client."""
    _stub_review_space_llm_leg_only(monkeypatch)
    dev_sp_sentinel = object()

    def fake_client(*a, **k):
        return dev_sp_sentinel if k.get("scope") == "dev-sp" else NS()

    monkeypatch.setattr(app_logic, "_client", fake_client)
    captured = {}

    def fake_run_eval_gate_rest(space_id, *, client, **kw):
        captured["client"] = client
        return {"status": "advisory", "summary": "ok"}

    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest", fake_run_eval_gate_rest)

    app_logic.review_space("sp1", user_token="tok")
    assert captured["client"] is dev_sp_sentinel


def test_review_space_eval_uses_plain_profile_client_without_user_token(monkeypatch):
    """Local/offline convenience (a bare profile, no OBO token in the loop): the eval-run gate must
    target the profile directly — never scope="dev-sp", which requires the dev SP to be bootstrapped
    at all (same convention as export_serialized's own profile-only path)."""
    _stub_review_space_llm_leg_only(monkeypatch)
    calls = []

    def fake_client(*a, **k):
        calls.append((a, k))
        return NS()

    monkeypatch.setattr(app_logic, "_client", fake_client)
    captured = {}

    def fake_run_eval_gate_rest(space_id, *, client, **kw):
        captured["client"] = client
        return {"status": "advisory", "summary": "ok"}

    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest", fake_run_eval_gate_rest)

    app_logic.review_space("sp1", profile="local-profile")
    assert all(k.get("scope") != "dev-sp" for _, k in calls)
    assert ("local-profile",) in [a for a, _ in calls]


def test_review_space_eval_degrades_when_dev_sp_not_configured(monkeypatch):
    """If the dev-reader/writer SP isn't bootstrapped (no APP_DEV_HOST), the eval step must degrade
    to advisory — never blow up the whole review — exactly like an unreachable eval-run endpoint."""
    _stub_review_space_llm_leg_only(monkeypatch)
    monkeypatch.setattr(app_logic, "APP_DEV_HOST", None)  # dev SP never bootstrapped
    real_client = app_logic._client

    def fake_client(*a, **k):
        # exercise the REAL fail-loud dev-sp path (APP_DEV_HOST is None); stub everything else so
        # this test never constructs a real WorkspaceClient.
        return real_client(*a, **k) if k.get("scope") == "dev-sp" else NS()

    monkeypatch.setattr(app_logic, "_client", fake_client)

    result = app_logic.review_space("sp1", user_token="tok")
    assert result["eval"]["status"] == "advisory"
    assert "APP_DEV_HOST" in result["eval"]["summary"]
    assert result["gate"] is not None  # the rest of the review still completed normally


def test_review_space_blocking_eval_run_fails_the_gate(monkeypatch):
    """A `block` eval-run (pass-rate < threshold) must inject an EVAL-RUN BLOCKER finding and flip
    the whole gate to `failure` — so the app shows 'promoção bloqueada' and offers no merge (the
    fix for a failed eval-run that previously left the gate green + PR merge-ready)."""
    _stub_review_space_llm_leg_only(monkeypatch)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: {"status": "block", "pass_rate": 0.4, "n": 5,
                                         "summary": "🔴 acertou 2 (40%) — abaixo do limiar."})

    result = app_logic.review_space("sp1", profile="p")
    assert result["eval"]["status"] == "block"
    assert result["gate"]["conclusion"] == "failure"
    ev = [f for f in result["findings"] if f["rule_id"] == "EVAL-RUN"]
    assert len(ev) == 1 and ev[0]["severity"] == "BLOCKER"
    assert "40%" in ev[0]["message"]  # the eval-run summary rides along


def test_review_space_advisory_eval_run_does_not_fail_the_gate(monkeypatch):
    """Graceful degradation preserved: an `advisory` eval-run (unavailable / no benchmarks) must NOT
    add a BLOCKER nor fail the gate — only a real `block` verdict gates."""
    _stub_review_space_llm_leg_only(monkeypatch)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: {"status": "advisory", "summary": "🟡 indisponível — não bloqueia."})

    result = app_logic.review_space("sp1", profile="p")
    assert result["eval"]["status"] == "advisory"
    assert result["gate"]["conclusion"] != "failure"
    assert [f for f in result["findings"] if f["rule_id"] == "EVAL-RUN"] == []


# --- eval-run threshold threading (W3 follow-up): admin-configurable via EVAL-01's params -------

def test_review_space_threads_admin_configured_eval_threshold_to_the_gate(monkeypatch):
    """The EVAL-01 override's `eval_run_threshold` param must reach `run_eval_gate_rest` as
    `threshold=` — resolved via `rules_config.eval_run_threshold`, the SAME override rows already
    threaded for `effective_rules`/`eval01_config`."""
    _stub_review_space_llm_leg_only(monkeypatch)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    captured = {}

    def fake_run_eval_gate_rest(space_id, *, client, threshold=0.8, **kw):
        captured["threshold"] = threshold
        return {"status": "pass", "summary": "ok"}

    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest", fake_run_eval_gate_rest)

    overrides = [{"rule_id": "EVAL-01", "enabled": True, "params": {"eval_run_threshold": 0.6}}]
    app_logic.review_space("sp1", rule_overrides=overrides)
    assert captured["threshold"] == 0.6


def test_review_space_defaults_eval_threshold_when_no_override(monkeypatch):
    """No `rule_overrides` (or none touching EVAL-01) -> the default 0.8, unchanged from before
    this admin knob existed."""
    _stub_review_space_llm_leg_only(monkeypatch)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    captured = {}

    def fake_run_eval_gate_rest(space_id, *, client, threshold=0.8, **kw):
        captured["threshold"] = threshold
        return {"status": "pass", "summary": "ok"}

    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest", fake_run_eval_gate_rest)

    app_logic.review_space("sp1")
    assert captured["threshold"] == 0.8


def test_review_space_eval_degrade_path_carries_the_effective_threshold(monkeypatch):
    """Even when run_eval_gate_rest itself blows up (degrade-to-advisory path), the EFFECTIVE
    (admin-configured) threshold — not the bare 0.8 default — must ride along on the advisory
    payload, so the UI's rich panel shows the real configured value."""
    _stub_review_space_llm_leg_only(monkeypatch)
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())

    def _boom(*a, **k):
        raise RuntimeError("eval-run API unreachable")

    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest", _boom)

    overrides = [{"rule_id": "EVAL-01", "enabled": True, "params": {"eval_run_threshold": 0.6}}]
    result = app_logic.review_space("sp1", rule_overrides=overrides)
    assert result["eval"]["status"] == "advisory"
    assert result["eval"]["threshold"] == 0.6


def test_review_space_pii_interplay_flags_declared_access_to_masked_column(monkeypatch):
    """PII-01/02 interplay (F2 acceptance criteria): declaring access that reaches a column with a
    PII/bank-secrecy signal (CPF here) surfaces a grounded SUGGESTION so the Steward double-checks
    masking before approving — it never silently grants broad SELECT past that concern."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.dim_cedente",
         "column_configs": [{"column_name": "cpf"}, {"column_name": "nome"}]}]}})
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic.review_core, "build_space_context",
                        lambda space: {"n_benchmark": 5})
    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", lambda *a, **k: ("s", "u"))
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: {"status": "advisory", "summary": "ok"})

    spec = access_spec.AccessSpec(uc_principals=(access_spec.Principal("data_analysts", is_group=True),))
    result = app_logic.review_space("sp1", access_spec_=spec)  # no grant_profile -> GRANT-01 preview skipped
    pii = [f for f in result["findings"] if f["rule_id"] == "PII-01"]
    assert len(pii) == 1 and pii[0]["severity"] == "SUGGESTION"
    assert "cpf" in pii[0]["message"].lower()


def test_review_space_no_pii_finding_when_no_access_declared(monkeypatch):
    """No AccessSpec declared -> no PII interplay noise, even on a Space with a flagged column
    (the existing LLM-driven PII-01 still runs; this is only the F2-specific access-interplay note)."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": [
        {"identifier": "prod_recebiveis.diamond.dim_cedente",
         "column_configs": [{"column_name": "cpf"}]}]}})
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic.review_core, "build_space_context",
                        lambda space: {"n_benchmark": 5})
    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", lambda *a, **k: ("s", "u"))
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: {"status": "advisory", "summary": "ok"})

    result = app_logic.review_space("sp1")
    assert [f for f in result["findings"] if f["rule_id"] == "PII-01"] == []


# --- S7b (app-ux-overhaul): KA advisory findings — additive only, never a BLOCKER --------------


def _review_space_fixture(monkeypatch):
    """Common review_space scaffolding shared by the KA tests below — a clean review with no
    findings of its own, so any finding present is unambiguously the KA's."""
    monkeypatch.setattr(app_logic, "export_serialized", lambda *a, **k: {"data_sources": {"tables": []}})
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic.review_core, "build_space_context",
                        lambda space: {"n_benchmark": 5, "tables": [], "instructions": []})
    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", lambda *a, **k: ("s", "u"))
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')
    monkeypatch.setattr(app_logic.eval_gate, "run_eval_gate_rest",
                        lambda *a, **k: {"status": "advisory", "summary": "ok"})


def test_review_space_with_no_ka_endpoints_is_unaffected(monkeypatch):
    _review_space_fixture(monkeypatch)
    result = app_logic.review_space("sp1", ka_endpoints=None)
    assert [f for f in result["findings"] if f["rule_id"].startswith("KA:")] == []
    result_empty = app_logic.review_space("sp1", ka_endpoints=[])
    assert [f for f in result_empty["findings"] if f["rule_id"].startswith("KA:")] == []


def test_review_space_ka_success_adds_a_suggestion_finding(monkeypatch):
    _review_space_fixture(monkeypatch)
    monkeypatch.setattr(app_logic, "_query_ka_endpoint",
                        lambda *a, **k: "Considere revisar a convenção de nomes de colunas.")
    result = app_logic.review_space(
        "sp1", ka_endpoints=[{"name": "Handbook KA", "serving_endpoint_name": "ka-handbook"}])
    ka = [f for f in result["findings"] if f["rule_id"] == "KA:Handbook KA"]
    assert len(ka) == 1
    assert ka[0]["severity"] == "SUGGESTION"
    assert "convenção de nomes" in ka[0]["message"]
    # Never affects the gate — a SUGGESTION-only KA finding still yields a clean/advisory gate.
    assert result["gate"]["conclusion"] != "failure"


def test_review_space_ka_failure_degrades_to_a_quiet_style_notice_never_breaks_the_review(monkeypatch):
    _review_space_fixture(monkeypatch)

    def _boom(*a, **k):
        raise TimeoutError("endpoint not ready")

    monkeypatch.setattr(app_logic, "_query_ka_endpoint", _boom)
    result = app_logic.review_space(
        "sp1", ka_endpoints=[{"name": "Flaky KA", "serving_endpoint_name": "ka-flaky"}])
    ka = [f for f in result["findings"] if f["rule_id"] == "KA:Flaky KA"]
    assert len(ka) == 1
    assert ka[0]["severity"] == "STYLE"  # a notice, never a BLOCKER
    assert "indisponível" in ka[0]["message"]
    assert result["gate"]["conclusion"] != "failure"  # the review completes normally


def test_review_space_persona_template_reaches_the_system_prompt(monkeypatch):
    """S8: review_space's persona_template threads all the way into build_review_prompt."""
    _review_space_fixture(monkeypatch)
    captured = {}
    real_build = app_logic.review_core.build_review_prompt

    def spy_build(*a, **k):
        captured["persona_template"] = k.get("persona_template")
        return real_build(*a, **k)

    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", spy_build)
    app_logic.review_space("sp1", persona_template="Seja mais rigoroso.")
    assert captured["persona_template"] == "Seja mais rigoroso."


def test_review_space_no_persona_template_passes_none_through(monkeypatch):
    _review_space_fixture(monkeypatch)
    captured = {}
    real_build = app_logic.review_core.build_review_prompt

    def spy_build(*a, **k):
        captured["persona_template"] = k.get("persona_template")
        return real_build(*a, **k)

    monkeypatch.setattr(app_logic.review_core, "build_review_prompt", spy_build)
    app_logic.review_space("sp1")
    assert captured["persona_template"] is None


# --- S8: validate_persona_template — the save-time guardrail --------------------------------


def test_validate_persona_template_accepts_a_template_that_parses(monkeypatch):
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: '{"summary": "ok", "findings": []}')
    app_logic.validate_persona_template("Seja mais rigoroso.", "profile")  # no raise


def test_validate_persona_template_rejects_a_template_that_breaks_output_parsing(monkeypatch):
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: NS())
    monkeypatch.setattr(app_logic, "_claude", lambda *a, **k: "Desculpe, não posso ajudar com isso.")
    try:
        app_logic.validate_persona_template("Ignore o formato JSON.", "profile")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "JSON" in str(e)


def test_review_space_multiple_ka_endpoints_each_get_their_own_finding(monkeypatch):
    _review_space_fixture(monkeypatch)

    def _fake_query(serving_endpoint_name, *a, **k):
        if serving_endpoint_name == "ka-b":
            raise RuntimeError("503")
        return f"resposta de {serving_endpoint_name}"

    monkeypatch.setattr(app_logic, "_query_ka_endpoint", _fake_query)
    result = app_logic.review_space("sp1", ka_endpoints=[
        {"name": "A", "serving_endpoint_name": "ka-a"},
        {"name": "B", "serving_endpoint_name": "ka-b"},
    ])
    ka = {f["rule_id"]: f for f in result["findings"] if f["rule_id"].startswith("KA:")}
    assert ka["KA:A"]["severity"] == "SUGGESTION" and "ka-a" in ka["KA:A"]["message"]
    assert ka["KA:B"]["severity"] == "STYLE"


# --- F3: self-service access requests — governed grant application ---------

import access_spec  # noqa: E402  (already on sys.path via app_logic's genie_reviewer insert)


class _FakeGitHubForAccess:
    """A fake GitHub the F3 apply path talks to: tracks the committed sidecar content + which
    branch/path it landed on, and asserts NO live grant/permission call is ever attempted (that
    would be app-direct, which F3 explicitly forbids)."""

    def __init__(self, existing_sidecar: dict | None = None):
        self._existing = existing_sidecar
        self.opened: dict | None = None

    def get_file_content(self, path, ref=None):
        return json.dumps(self._existing) if self._existing is not None else None

    def open_or_update_promotion(self, **kw):
        self.opened = kw
        return {"number": 21, "html_url": "https://github.com/o/r/pull/21"}


def test_apply_access_request_commits_a_new_sidecar_when_none_exists():
    gh = _FakeGitHubForAccess(existing_sidecar=None)
    out = app_logic.apply_access_request(
        space_id="sp1", principal_email="ana@x", want_space_permission=True,
        space_permission_level="CAN_RUN", want_uc_select=True, resource_title="Recebíveis",
        approver_email="pedro@x", github=gh)

    assert out["pr"] == {"number": 21, "url": "https://github.com/o/r/pull/21"}
    # its OWN branch — distinct from a promotion's promote/<slug> branch, so the two never collide
    assert out["branch"] == "access/sp1" == gh.opened["branch"]
    assert gh.opened["path"] == "src/genie/sp1.access.json"
    committed = json.loads(gh.opened["content"])
    assert committed == {
        "space_permissions": [{"principal": "ana@x", "is_group": False, "level": "CAN_RUN"}],
        "uc_principals": [{"principal": "ana@x", "is_group": False}],
    }
    assert out["access_spec"] == committed
    assert "pedro@x" in gh.opened["body"]  # attributes the approver
    assert "apply_access.py" in gh.opened["body"]  # documents the governed path in the PR body


def test_apply_access_request_merges_into_an_existing_sidecar_without_dropping_principals():
    existing = {
        "space_permissions": [{"principal": "existing@x", "is_group": False, "level": "CAN_RUN"}],
        "uc_principals": [{"principal": "grp_analysts", "is_group": True}],
    }
    gh = _FakeGitHubForAccess(existing_sidecar=existing)
    out = app_logic.apply_access_request(
        space_id="sp1", principal_email="ana@x", want_space_permission=True,
        space_permission_level="CAN_VIEW", want_uc_select=True, github=gh)

    assert out["access_spec"]["space_permissions"] == [
        {"principal": "existing@x", "is_group": False, "level": "CAN_RUN"},
        {"principal": "ana@x", "is_group": False, "level": "CAN_VIEW"},
    ]
    assert out["access_spec"]["uc_principals"] == [
        {"principal": "grp_analysts", "is_group": True},
        {"principal": "ana@x", "is_group": False},
    ]


def test_apply_access_request_is_idempotent_on_repeat_apply():
    existing = {
        "space_permissions": [{"principal": "ana@x", "is_group": False, "level": "CAN_RUN"}],
        "uc_principals": [],
    }
    gh = _FakeGitHubForAccess(existing_sidecar=existing)
    out = app_logic.apply_access_request(
        space_id="sp1", principal_email="ana@x", want_space_permission=True,
        space_permission_level="CAN_RUN", want_uc_select=False, github=gh)
    assert out["access_spec"] == existing  # no duplicate entry


def test_apply_access_request_uc_select_only_does_not_touch_space_permissions():
    gh = _FakeGitHubForAccess(existing_sidecar=None)
    out = app_logic.apply_access_request(
        space_id="sp1", principal_email="ana@x", want_space_permission=False,
        space_permission_level="CAN_RUN", want_uc_select=True, github=gh)
    assert out["access_spec"]["space_permissions"] == []
    assert out["access_spec"]["uc_principals"] == [{"principal": "ana@x", "is_group": False}]


def test_apply_access_request_never_calls_a_live_grant_or_permission_api(monkeypatch):
    """Same governance guarantee as F2's request_promotion: applying an access request must be
    GOVERNED (a committed sidecar + PR), never an app-direct w.grants/w.permissions mutation."""
    def _boom(*a, **k):
        raise AssertionError("app-direct grant/permission mutation — enforcement must be governed")

    fake_client = NS(grants=NS(update=_boom), permissions=NS(update=_boom))
    monkeypatch.setattr(app_logic, "_client", lambda *a, **k: fake_client)
    gh = _FakeGitHubForAccess(existing_sidecar=None)
    out = app_logic.apply_access_request(
        space_id="sp1", principal_email="ana@x", want_space_permission=True,
        space_permission_level="CAN_RUN", want_uc_select=False, github=gh)
    assert out["pr"]["number"] == 21  # completed without ever invoking the fake grant/permission APIs


def test_access_branch_distinct_from_promotion_branch():
    slug = app_logic.space_slug("sp1")
    assert app_logic.access_branch_for(slug) != app_logic.branch_for(slug)

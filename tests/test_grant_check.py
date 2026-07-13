"""Unit tests for the deterministic GRANT-01 check (S5). Getter is injected — no workspace."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import grant_check  # noqa: E402

SPACE = {"data_sources": {"tables": [
    {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
    {"identifier": "prod_recebiveis.diamond.dim_arranjo"},
]}}
GROUP = "grp_recebiveis_consumer"


def test_no_findings_when_group_can_select():
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["USE_SCHEMA", "SELECT"]}],
    }
    assert grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq]) == []


def test_all_privileges_counts_as_select():
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["ALL_PRIVILEGES"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["ALL_PRIVILEGES"]}],
    }
    assert grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq]) == []


def test_flags_missing_grant():
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": "someone_else", "privileges": ["SELECT"]}],
    }
    out = grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq])
    assert len(out) == 1
    assert out[0]["rule_id"] == "GRANT-01" and out[0]["severity"] == "BLOCKER"
    assert "dim_arranjo" in out[0]["table"]


def test_lookup_error_becomes_finding():
    def boom(fq):
        raise RuntimeError("permission denied")
    out = grant_check.check_grants(SPACE, GROUP, boom)
    assert len(out) == 2 and all(f["rule_id"] == "GRANT-01" for f in out)


# --- F2: extended to ALL declared principals (not just one consumer group) ---

def test_all_declared_principals_pass_when_all_can_select():
    """F2: check_grants accepts an iterable of principals (every group/user in an AccessSpec) —
    passes cleanly when every one of them can SELECT every table."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [
            {"principal": GROUP, "privileges": ["SELECT"]},
            {"principal": "ana@x.com", "privileges": ["SELECT"]},
        ],
        "prod_recebiveis.diamond.dim_arranjo": [
            {"principal": GROUP, "privileges": ["SELECT"]},
            {"principal": "ana@x.com", "privileges": ["SELECT"]},
        ],
    }
    out = grant_check.check_grants(SPACE, [GROUP, "ana@x.com"], lambda fq: grants[fq])
    assert out == []


def test_blocks_when_any_declared_principal_lacks_select():
    """A missing grant for ANY ONE declared principal is a BLOCKER — F2 acceptance criteria: a
    missing grant for a newly-declared principal must fail the PR just like the base group would."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["SELECT"]}],
    }
    out = grant_check.check_grants(SPACE, [GROUP, "ana@x.com"], lambda fq: grants[fq])
    assert len(out) == 2  # ana@x.com missing SELECT on both tables
    assert all(f["rule_id"] == "GRANT-01" and f["severity"] == "BLOCKER" for f in out)
    assert all(f["principal"] == "ana@x.com" for f in out)


def test_effective_grants_fetched_once_per_table_regardless_of_principal_count():
    """A multi-principal AccessSpec must not multiply UC calls — get_effective_grants is called
    ONCE per table no matter how many principals are checked against its result."""
    calls = []

    def counting_getter(fq):
        calls.append(fq)
        return [{"principal": p, "privileges": ["SELECT"]} for p in [GROUP, "ana@x.com", "grp2"]]

    out = grant_check.check_grants(SPACE, [GROUP, "ana@x.com", "grp2"], counting_getter)
    assert out == []
    assert len(calls) == len(SPACE["data_sources"]["tables"])  # once per table, not per principal


def test_duplicate_and_falsy_principals_are_deduped_and_dropped():
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["SELECT"]}],
    }
    out = grant_check.check_grants(SPACE, [GROUP, GROUP, "", None], lambda fq: grants[fq])
    assert out == []  # no phantom finding for "" / None, and no double-count of GROUP


def test_single_string_consumer_group_still_works_backcompat():
    """Legacy call sites pass a bare string (not an iterable of principals) — must still work."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["SELECT"]}],
    }
    assert grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq]) == []


# --- G9: apply_access-aware split — baseline stays BLOCKER, declared becomes advisory ---------

def test_is_uc_grantable_group_account_type_is_grantable():
    assert grant_check.is_uc_grantable_group("Group", "data_analysts") is True


def test_is_uc_grantable_group_workspace_local_type_is_not_grantable():
    assert grant_check.is_uc_grantable_group("WorkspaceGroup", "any_custom_local_group") is False


def test_is_uc_grantable_group_unverifiable_falls_back_to_builtin_name_exclusion():
    """No SCIM meta at all (lookup miss) — only the well-known built-ins are excluded by name; an
    unverifiable CUSTOM group name is never blindly trusted OR blindly rejected on name alone."""
    assert grant_check.is_uc_grantable_group(None, "users") is False
    assert grant_check.is_uc_grantable_group(None, "admins") is False
    assert grant_check.is_uc_grantable_group(None, "data_analysts") is True


def test_declared_principal_missing_select_is_advisory_not_blocker():
    """G9: a DECLARED (AccessSpec) principal missing SELECT is a non-blocking SUGGESTION — the
    pipeline's own apply_access grants it at deploy, so blocking pre-merge on it is contradictory."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["SELECT"]}],
    }
    out = grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq], declared_principals=["ana@x.com"])
    assert len(out) == 2  # one per table
    assert all(f["severity"] == "SUGGESTION" and f["rule_id"] == "GRANT-01" for f in out)
    assert all(f["principal"] == "ana@x.com" for f in out)
    assert all("apply_access" in f["message"] and "deploy" in f["message"] for f in out)


def test_baseline_missing_select_stays_blocker_when_declared_also_missing():
    """Misto: a baseline-miss AND a declared-miss together — the baseline one still blocks
    (BLOCKER), the declared one is merely advisory (SUGGESTION); the output distinguishes both."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [],
        "prod_recebiveis.diamond.dim_arranjo": [],
    }
    out = grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq], declared_principals=["ana@x.com"])
    by_principal = {(f["principal"], f["severity"]) for f in out}
    assert (GROUP, "BLOCKER") in by_principal
    assert ("ana@x.com", "SUGGESTION") in by_principal
    assert any(f["severity"] == "BLOCKER" for f in out)  # a caller's decide_gate would still fail


def test_declared_principal_already_in_baseline_is_not_double_reported():
    """A principal declared again that's already the baseline group is checked ONLY as baseline
    (BLOCKER), never ALSO as a separate advisory finding for the same (table, principal)."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [],
        "prod_recebiveis.diamond.dim_arranjo": [],
    }
    out = grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq], declared_principals=[GROUP])
    assert len(out) == 2  # not 4 — GROUP is never checked twice
    assert all(f["severity"] == "BLOCKER" for f in out)


def test_non_grantable_declared_principal_gets_the_not_grantable_advisory():
    """A declared principal apply_access could never actually grant (a workspace-local group) gets
    a message saying so — not a promise ('será concedido no deploy') the pipeline can't keep."""
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [{"principal": GROUP, "privileges": ["SELECT"]}],
        "prod_recebiveis.diamond.dim_arranjo": [{"principal": GROUP, "privileges": ["SELECT"]}],
    }
    out = grant_check.check_grants(
        SPACE, GROUP, lambda fq: grants[fq],
        declared_principals=["users"], non_grantable_principals=["users"])
    assert len(out) == 2
    assert all(f["severity"] == "SUGGESTION" and f["principal"] == "users" for f in out)
    assert all("grupo local do workspace" in f["message"] for f in out)
    assert all("apply_access" not in f["message"] for f in out)  # never the "will be granted" promise


def test_declared_principal_that_can_select_produces_no_finding():
    grants = {
        "prod_recebiveis.diamond.fato_recebiveis": [
            {"principal": GROUP, "privileges": ["SELECT"]},
            {"principal": "ana@x.com", "privileges": ["SELECT"]},
        ],
        "prod_recebiveis.diamond.dim_arranjo": [
            {"principal": GROUP, "privileges": ["SELECT"]},
            {"principal": "ana@x.com", "privileges": ["SELECT"]},
        ],
    }
    out = grant_check.check_grants(SPACE, GROUP, lambda fq: grants[fq], declared_principals=["ana@x.com"])
    assert out == []

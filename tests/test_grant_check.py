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

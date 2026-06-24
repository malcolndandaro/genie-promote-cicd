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

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import audience_check  # noqa: E402
import audience_spec  # noqa: E402


SPEC = audience_spec.AudienceSpec((
    audience_spec.AudiencePrincipal("finance-users", is_group=True),
    audience_spec.AudiencePrincipal("ana@example.com"),
))
SPACE = {"data_sources": {"tables": [{"identifier": "prod_recebiveis.diamond.fato"}]}}


def test_missing_select_is_informational_terraform_guidance():
    findings = audience_check.check_audience(
        SPACE, SPEC,
        lambda _table: [{"principal": "finance-users", "privileges": ["SELECT"]}],
        principal_exists=lambda _name, _is_group: True,
        table_exists=lambda _table: True,
    )
    assert len(findings) == 1
    assert findings[0]["severity"] == "SUGGESTION"
    assert findings[0]["principal"] == "ana@example.com"
    assert "Terraform" in findings[0]["message"]


def test_invalid_principal_and_table_are_content_blockers():
    findings = audience_check.check_audience(
        SPACE, SPEC, lambda _table: [],
        principal_exists=lambda name, _is_group: name != "finance-users",
        table_exists=lambda _table: False,
    )
    blockers = [finding for finding in findings if finding["severity"] == "BLOCKER"]
    assert {finding.get("principal") for finding in blockers} >= {"finance-users"}
    assert {finding.get("table") for finding in blockers} >= {"prod_recebiveis.diamond.fato"}


def test_inspection_outage_is_operational_not_author_blocker():
    def unavailable(_table):
        raise TimeoutError("grants API timeout")

    findings = audience_check.check_audience(
        SPACE, SPEC, unavailable,
        principal_exists=lambda _name, _is_group: True,
        table_exists=lambda _table: True,
    )
    assert [finding["severity"] for finding in findings] == ["OPERATIONAL"]
    assert "KIP" in findings[0]["suggestion"]


def test_malformed_table_identifier_blocks_before_grants_lookup():
    called = False

    def grants(_table):
        nonlocal called
        called = True
        return []

    findings = audience_check.check_audience(
        {"data_sources": {"tables": [{"identifier": "not-qualified"}]}}, SPEC, grants,
        principal_exists=lambda _name, _is_group: True,
    )
    assert any(f["severity"] == "BLOCKER" for f in findings)
    assert not called

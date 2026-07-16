import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
import audience_spec  # noqa: E402


def test_audience_is_required_deduplicated_and_serialized_in_stable_order():
    with pytest.raises(ValueError, match="at least one"):
        audience_spec.AudienceSpec(())
    with pytest.raises(ValueError, match="duplicate"):
        audience_spec.AudienceSpec((
            audience_spec.AudiencePrincipal("Finance"),
            audience_spec.AudiencePrincipal(" finance ", is_group=True),
        ))
    spec = audience_spec.AudienceSpec((
        audience_spec.AudiencePrincipal("z-user"),
        audience_spec.AudiencePrincipal("Alpha Group", is_group=True),
    ))
    assert spec.to_dict() == {"principals": [
        {"principal": "Alpha Group", "is_group": True},
        {"principal": "z-user", "is_group": False},
    ]}


def test_wire_contract_never_accepts_a_permission_level():
    with pytest.raises(ValueError, match="always derives CAN_RUN"):
        audience_spec.AudienceSpec.from_dict({
            "principals": [{"principal": "users", "is_group": True, "level": "CAN_VIEW"}]
        })


def test_legacy_translation_keeps_can_run_discards_uc_and_rejects_can_view():
    warnings = []
    translated = audience_spec.from_legacy_access_spec({
        "space_permissions": [
            {"principal": "users", "is_group": True, "level": "CAN_RUN"},
        ],
        "uc_principals": [{"principal": "ana@example.com", "is_group": False}],
    }, warn=warnings.append)
    assert translated.to_dict() == {
        "principals": [{"principal": "users", "is_group": True}]
    }
    assert warnings and "discarded" in warnings[0]

    with pytest.raises(audience_spec.LegacyAudienceError, match="CAN_VIEW"):
        audience_spec.from_legacy_access_spec({
            "space_permissions": [
                {"principal": "users", "is_group": True, "level": "CAN_VIEW"},
            ]
        })


def test_legacy_uc_only_declaration_is_not_silently_promoted_to_audience():
    with pytest.raises(audience_spec.LegacyAudienceError, match="at least one"):
        audience_spec.from_legacy_access_spec({
            "space_permissions": [],
            "uc_principals": [{"principal": "ana@example.com"}],
        })

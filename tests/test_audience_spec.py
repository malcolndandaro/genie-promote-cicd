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
    with pytest.raises(ValueError, match="permission level"):
        audience_spec.AudienceSpec.from_dict({
            "principals": [{"principal": "users", "is_group": True, "level": "CAN_EDIT"}]
        })


def test_sidecar_parser_accepts_only_the_canonical_shape():
    spec = audience_spec.parse_sidecar({
        "principals": [{"principal": "users", "is_group": True}]
    })
    assert spec.names() == ("users",)
    with pytest.raises(ValueError, match="principals"):
        audience_spec.parse_sidecar({"entries": []})

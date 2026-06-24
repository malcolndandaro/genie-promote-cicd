"""Unit tests for the pre-render + identifier allowlist (S4). Pure — no Databricks."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pre_render  # noqa: E402

DEV = json.dumps(
    {
        "version": 1,
        "data_sources": {
            "tables": [
                {"identifier": "dev_recebiveis.diamond.fato_recebiveis"},
                {"identifier": "dev_recebiveis.diamond.dim_cedente"},
            ]
        },
        "instructions": {
            "example_question_sqls": [
                {"sql": ["SELECT * FROM dev_recebiveis.diamond.fato_recebiveis"]}
            ]
        },
    },
    ensure_ascii=False,
)


def test_rebind_swaps_every_ref():
    out = pre_render.rebind(DEV, "dev", "prod", "recebiveis")
    assert "dev_recebiveis" not in out
    assert out.count("prod_recebiveis") == 3  # 2 identifiers + 1 in SQL


def test_rebind_keeps_valid_json():
    out = pre_render.rebind(DEV, "dev", "prod", "recebiveis")
    json.loads(out)  # must not raise


def test_rebind_idempotent_once_prod():
    out = pre_render.rebind(DEV, "dev", "prod", "recebiveis")
    assert pre_render.rebind(out, "dev", "prod", "recebiveis") == out


def test_check_passes_on_clean_prod():
    out = pre_render.rebind(DEV, "dev", "prod", "recebiveis")
    assert pre_render.find_violations(out, "prod", "recebiveis") == []


def test_check_flags_dev_leak():
    assert "dev_recebiveis" in pre_render.find_violations(DEV, "prod", "recebiveis")


def test_check_flags_sbx_leak_in_sql():
    leak = DEV.replace(
        "dev_recebiveis.diamond.dim_cedente", "sbx_recebiveis.diamond.dim_cedente"
    )
    v = pre_render.find_violations(leak, "prod", "recebiveis")
    assert "sbx_recebiveis" in v and "dev_recebiveis" in v


def test_domain_is_configurable():
    src = json.dumps({"t": "dev_merchants.gold.m"})
    out = pre_render.rebind(src, "dev", "prod", "merchants")
    assert "prod_merchants" in out and "dev_merchants" not in out


# --- adversarial cases from the S4 review (true allowlist must catch these) ---
def test_check_flags_backtick_foreign_catalog():
    blob = json.dumps({"sql": ["SELECT * FROM `sbx_recebiveis`.`diamond`.`t`"]})
    assert "sbx_recebiveis" in pre_render.find_violations(blob, "prod", "recebiveis")


def test_check_flags_uppercase_env():
    blob = json.dumps({"id": "DEV_recebiveis.diamond.t"})
    assert pre_render.find_violations(blob, "prod", "recebiveis")  # non-empty


def test_check_flags_unrelated_foreign_catalog():
    blob = json.dumps({"id": "samples.nyctaxi.trips"})
    assert "samples" in pre_render.find_violations(blob, "prod", "recebiveis")


def test_real_fixture_round_trips_clean():
    src = os.path.join(
        os.path.dirname(__file__), "..", "src", "genie", "receivables.serialized_space.json"
    )
    raw = open(src, encoding="utf-8").read()
    rendered = pre_render.rebind(raw, "dev", "prod", "recebiveis")
    assert pre_render.find_violations(rendered, "prod", "recebiveis") == []
    assert pre_render.find_violations(raw, "prod", "recebiveis") == ["dev_recebiveis"]

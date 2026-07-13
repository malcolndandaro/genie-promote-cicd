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


# --- A3: bidirectional rebind — dev->prod (promote) AND prod->dev (rehydrate) ------------------
#
# `rebind`/`find_violations` were already pure + config-driven (from_env/to_env are plain
# arguments, not literals) — A3 doesn't change the functions, it PROVES the reverse direction with
# the same rigor as the forward (promote) direction, including the adversarial allowlist cases.

PROD = json.dumps(
    {
        "version": 1,
        "data_sources": {
            "tables": [
                {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
                {"identifier": "prod_recebiveis.diamond.dim_cedente"},
            ]
        },
        "instructions": {
            "example_question_sqls": [
                {"sql": ["SELECT * FROM prod_recebiveis.diamond.fato_recebiveis"]}
            ]
        },
    },
    ensure_ascii=False,
)

# One table of {name, source, from_env, to_env} driving both directions with identical assertions,
# so a regression in either direction fails the same way.
_DIRECTIONS = [
    {"label": "dev->prod (promote)", "source": DEV, "from_env": "dev", "to_env": "prod"},
    {"label": "prod->dev (rehydrate)", "source": PROD, "from_env": "prod", "to_env": "dev"},
]


def test_bidirectional_rebind_swaps_every_ref():
    for case in _DIRECTIONS:
        out = pre_render.rebind(case["source"], case["from_env"], case["to_env"], "recebiveis")
        assert f"{case['from_env']}_recebiveis" not in out, case["label"]
        assert out.count(f"{case['to_env']}_recebiveis") == 3, case["label"]  # 2 identifiers + 1 SQL


def test_bidirectional_rebind_keeps_valid_json():
    for case in _DIRECTIONS:
        out = pre_render.rebind(case["source"], case["from_env"], case["to_env"], "recebiveis")
        json.loads(out)  # must not raise


def test_bidirectional_check_passes_on_clean_target():
    for case in _DIRECTIONS:
        out = pre_render.rebind(case["source"], case["from_env"], case["to_env"], "recebiveis")
        assert pre_render.find_violations(out, case["to_env"], "recebiveis") == [], case["label"]


def test_bidirectional_check_flags_source_env_leak():
    # Un-rebound source (still shaped for the OTHER env) must fail the target's own allowlist.
    for case in _DIRECTIONS:
        leaks = pre_render.find_violations(case["source"], case["to_env"], "recebiveis")
        assert f"{case['from_env']}_recebiveis" in leaks, case["label"]


def test_bidirectional_reverse_allowlist_catches_a_stray_foreign_catalog():
    # The reverse-allowlist requirement (A3 AC): a THIRD-environment/foreign catalog reference
    # (unaffected by the from_env->to_env rebind, so it survives untouched) must be caught by
    # find_violations in BOTH directions — not just the historically-tested dev->prod one. (A ref to
    # the SOURCE env is already covered by test_bidirectional_check_flags_source_env_leak.)
    for case in _DIRECTIONS:
        poisoned = case["source"].replace(
            f"{case['from_env']}_recebiveis.diamond.dim_cedente",
            "sbx_recebiveis.diamond.dim_cedente",
        )
        rebound = pre_render.rebind(poisoned, case["from_env"], case["to_env"], "recebiveis")
        violations = pre_render.find_violations(rebound, case["to_env"], "recebiveis")
        assert "sbx_recebiveis" in violations, case["label"]


def test_bidirectional_domain_is_configurable():
    for case in _DIRECTIONS:
        src = json.dumps({"t": f"{case['from_env']}_merchants.gold.m"})
        out = pre_render.rebind(src, case["from_env"], case["to_env"], "merchants")
        assert f"{case['to_env']}_merchants" in out, case["label"]
        assert f"{case['from_env']}_merchants" not in out, case["label"]


def test_bidirectional_rebind_is_a_pure_inverse_round_trip():
    # rebind(dev->prod) then rebind(prod->dev) must reproduce the original — proves the two
    # directions are true inverses of each other, not independently-behaving one-way transforms.
    prod_shaped = pre_render.rebind(DEV, "dev", "prod", "recebiveis")
    back_to_dev = pre_render.rebind(prod_shaped, "prod", "dev", "recebiveis")
    assert json.loads(back_to_dev) == json.loads(DEV)


# --- G6: find_refs + apply_table_mapping (the rehydrate table de-para) -------------------------

PROD_WITH_TWO_TABLES = json.dumps(
    {
        "data_sources": {
            "tables": [
                {"identifier": "prod_recebiveis.diamond.fato_recebiveis"},
                {"identifier": "prod_recebiveis.diamond.dim_cedente"},
            ]
        },
        "instructions": {
            "example_question_sqls": [
                {"sql": ["SELECT * FROM prod_recebiveis.diamond.fato_recebiveis f "
                        "JOIN `prod_recebiveis`.`diamond`.`dim_cedente` c ON f.id = c.id"]}
            ]
        },
    },
    ensure_ascii=False,
)


def test_find_refs_returns_distinct_refs_in_first_appearance_order():
    refs = pre_render.find_refs(PROD_WITH_TWO_TABLES)
    assert refs == [
        "prod_recebiveis.diamond.fato_recebiveis",
        "prod_recebiveis.diamond.dim_cedente",
    ]  # the SQL join repeats both refs — de-duped, not doubled


def test_find_refs_strips_backticks():
    blob = json.dumps({"sql": ["SELECT * FROM `prod_recebiveis`.`diamond`.`dim_cedente`"]})
    assert pre_render.find_refs(blob) == ["prod_recebiveis.diamond.dim_cedente"]


def test_apply_table_mapping_default_is_a_no_op():
    # An IDENTITY mapping (target == the plain default) must round-trip unchanged — the UI only
    # ever sends OVERRIDDEN rows, but the function itself must be safe if a caller sends one anyway.
    rebound = pre_render.rebind(PROD_WITH_TWO_TABLES, "prod", "dev", "recebiveis")
    mapping = {"prod_recebiveis.diamond.fato_recebiveis": "dev_recebiveis.diamond.fato_recebiveis"}
    out = pre_render.apply_table_mapping(rebound, mapping, from_env="prod", to_env="dev", domain="recebiveis")
    assert json.loads(out) == json.loads(rebound)


def test_apply_table_mapping_overrides_the_identifier_and_the_sql_occurrence():
    rebound = pre_render.rebind(PROD_WITH_TWO_TABLES, "prod", "dev", "recebiveis")
    mapping = {"prod_recebiveis.diamond.dim_cedente": "dev_recebiveis.diamond.dim_cedente_v2"}
    out = pre_render.apply_table_mapping(rebound, mapping, from_env="prod", to_env="dev", domain="recebiveis")
    body = json.loads(out)
    idents = [t["identifier"] for t in body["data_sources"]["tables"]]
    # the OTHER table's identifier is untouched; the MAPPED one is exactly the override, not a
    # partial/duplicated match (proves the old default target is fully gone, not just prefixed).
    assert idents == ["dev_recebiveis.diamond.fato_recebiveis", "dev_recebiveis.diamond.dim_cedente_v2"]
    # the SQL occurrence (backtick-quoted in the fixture) was ALSO rewritten, not just the identifier
    sql = body["instructions"]["example_question_sqls"][0]["sql"][0]
    assert "dev_recebiveis.diamond.dim_cedente_v2" in sql


def test_apply_table_mapping_matches_a_backtick_quoted_default_target():
    # The DEFAULT target (post-rebind) may itself appear backtick-quoted in the source JSON (as in
    # PROD_WITH_TWO_TABLES's SQL) — apply_table_mapping must match it regardless.
    rebound = pre_render.rebind(PROD_WITH_TWO_TABLES, "prod", "dev", "recebiveis")
    assert "`dev_recebiveis`.`diamond`.`dim_cedente`" in rebound  # sanity: the fixture backtick-quotes this one
    mapping = {"prod_recebiveis.diamond.dim_cedente": "dev_recebiveis.diamond.renamed"}
    out = pre_render.apply_table_mapping(rebound, mapping, from_env="prod", to_env="dev", domain="recebiveis")
    assert "dev_recebiveis.diamond.renamed" in out
    assert "dim_cedente" not in out


def test_apply_table_mapping_can_retarget_to_a_different_catalog():
    # A de-para may point at a catalog OTHER than the plain dev_<domain> default (e.g. a
    # differently-named dev catalog) — apply_table_mapping itself has no opinion on that; the
    # anti-leak invariant (never prod_<domain>) is enforced by the CALLER (app/rehydrate.py), not here.
    rebound = pre_render.rebind(PROD_WITH_TWO_TABLES, "prod", "dev", "recebiveis")
    mapping = {"prod_recebiveis.diamond.dim_cedente": "dev_sandbox.diamond.dim_cedente"}
    out = pre_render.apply_table_mapping(rebound, mapping, from_env="prod", to_env="dev", domain="recebiveis")
    assert "dev_sandbox.diamond.dim_cedente" in out


def test_find_violations_extra_allowed_catalogs_widens_the_allowlist():
    # A ref retargeted (via apply_table_mapping) to a catalog OTHER than dev_<domain> must NOT be
    # flagged once that catalog is explicitly allowed — but anything ELSE unlisted still is.
    blob = json.dumps({"a": "dev_recebiveis.x.y", "b": "dev_sandbox.x.y", "c": "sbx_recebiveis.x.y"})
    assert pre_render.find_violations(blob, "dev", "recebiveis") == ["dev_sandbox", "sbx_recebiveis"]
    widened = pre_render.find_violations(blob, "dev", "recebiveis", extra_allowed_catalogs={"dev_sandbox"})
    assert widened == ["sbx_recebiveis"]


def test_find_violations_extra_allowed_catalogs_does_not_exempt_the_source_env():
    # The widened allowlist is ADDITIVE — it must never accidentally re-open the door to the
    # source (prod) catalog just because SOME other override was allowed (app/rehydrate.py is the
    # one that explicitly refuses a prod-pointing override before ever calling find_violations, but
    # this pins that find_violations itself never silently exempts it either).
    blob = json.dumps({"a": "prod_recebiveis.x.y"})
    violations = pre_render.find_violations(blob, "dev", "recebiveis", extra_allowed_catalogs={"dev_sandbox"})
    assert violations == ["prod_recebiveis"]

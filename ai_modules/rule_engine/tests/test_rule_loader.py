from datetime import date

import pytest
import yaml

from ai_modules.rule_engine import (
    Condition,
    ConditionGroup,
    Provenance,
    RuleValidationError,
    compute_rule_hash,
    load_rules,
    parse_rule,
    validate_rule_set,
)

from .rule_factory import leaf, rule_dict


def errors_for(data) -> str:
    with pytest.raises(RuleValidationError) as info:
        parse_rule(data, origin="test.yaml")
    return "\n".join(info.value.errors)


def write_rules(tmp_path, *rules):
    for i, data in enumerate(rules):
        (tmp_path / f"rule_{i}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return tmp_path


def test_valid_rule_parses_with_hash():
    rule = parse_rule(rule_dict())
    assert rule.scheme_id == "test_scheme"
    assert rule.effective_from == date(2025, 1, 1)
    assert isinstance(rule.conditions[0], Condition)
    assert rule.conditions[0].min_trust is Provenance.SELF_DECLARED
    assert len(rule.rule_hash) == 64
    assert rule.rule_hash == compute_rule_hash(rule)


def test_rule_hash_is_stable_and_content_sensitive():
    assert parse_rule(rule_dict()).rule_hash == parse_rule(rule_dict()).rule_hash
    changed = rule_dict(conditions=[leaf("income_limit", "annual_family_income", "lte", 600001)])
    assert parse_rule(changed).rule_hash != parse_rule(rule_dict()).rule_hash


def test_unknown_operator_rejected():
    msg = errors_for(rule_dict(conditions=[leaf("c1", "age", "approx", 18)]))
    assert "test.yaml: $.conditions[0].operator: unknown operator 'approx'" in msg


def test_unknown_field_rejected():
    msg = errors_for(rule_dict(conditions=[leaf("c1", "caste", "eq", "obc")]))
    assert "$.conditions[0].field: unknown profile field 'caste'" in msg


@pytest.mark.parametrize(
    ("condition", "fragment"),
    [
        (
            leaf("c1", "social_category", "gte", "obc"),
            "operator 'gte' cannot be used on enum field 'social_category'",
        ),
        (leaf("c1", "family_owns_cultivable_land", "lte", 1), "cannot be used on boolean field"),
        (leaf("c1", "age", "is_true"), "operator 'is_true' cannot be used on integer field 'age'"),
        (leaf("c1", "annual_family_income", "lte", "600000"), "'600000' is not a valid number value"),
        (leaf("c1", "age", "gte", 17.5), "17.5 is not a valid integer value"),
        (leaf("c1", "annual_family_income", "lte", True), "True is not a valid number value"),
        (
            leaf("c1", "social_category", "in", ["obc", "OBC_X"]),
            "'OBC_X' is not a valid choice for 'social_category'",
        ),
        (leaf("c1", "social_category", "in", "obc"), "requires a list"),
        (leaf("c1", "social_category", "in", []), "list must not be empty"),
        (leaf("c1", "social_category", "in", ["obc", "obc"]), "duplicate values"),
        (leaf("c1", "age", "between", [40, 18]), "low bound 40 is greater than high bound 18"),
        (leaf("c1", "age", "between", [18]), "requires [low, high]"),
        (leaf("c1", "class12_percentage", "gte", 120), "must be <= 100"),
        (leaf("c1", "age", "gte"), "requires a value"),
        (leaf("c1", "has_samagra_id", "is_true", True), "takes no value"),
        (leaf("c1", "age", "gte", 18, min_trust="ocr"), "invalid value 'ocr'"),
        (leaf("c1", "age", "gte", 18, resolves_with=["aadhaar"]), "'aadhaar' is neither a document type id"),
        (leaf("c1", "age", "gte", 18, colour="red"), "$.conditions[0].colour: unknown key"),
        (
            {"id": "c1", "description": "x", "field": "age", "operator": "gte", "value": 1},
            "source_ref: required",
        ),
    ],
)
def test_condition_type_errors(condition, fragment):
    assert fragment in errors_for(rule_dict(conditions=[condition]))


def test_duplicate_condition_ids_rejected_across_groups():
    data = rule_dict(
        conditions=[
            leaf("dup", "age", "gte", 18),
            {"id": "grp", "description": "g", "any": [leaf("dup", "age", "lte", 40)]},
        ]
    )
    assert "$.conditions[1].any[0].id: duplicate condition id 'dup'" in errors_for(data)


def test_group_parsing_and_validation():
    data = rule_dict(
        conditions=[
            {
                "id": "grp",
                "description": "Either young or female",
                "any": [leaf("young", "age", "lte", 25), leaf("female", "gender", "eq", "female")],
            }
        ]
    )
    group = parse_rule(data).conditions[0]
    assert isinstance(group, ConditionGroup)
    assert [c.id for c in group.items] == ["young", "female"]
    both = rule_dict(
        conditions=[{"id": "g", "description": "g", "all": [leaf("a", "age", "gte", 1)], "any": []}]
    )
    assert "exactly one of 'all' or 'any'" in errors_for(both)
    empty = rule_dict(conditions=[{"id": "g", "description": "g", "all": []}])
    assert "$.conditions[0].all: must be a non-empty list" in errors_for(empty)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"version": 2025.1}, "must be a quoted version string"),
        ({"scheme_id": "PM-Kisan"}, "must be snake_case"),
        ({"level": "district"}, "$.level: invalid value 'district'"),
        ({"state": None}, "$.state: required when level is 'state'"),
        ({"effective_from": "01-01-2025"}, "must be a date in YYYY-MM-DD format"),
        ({"effective_to": "2024-12-31"}, "is before effective_from"),
        ({"verification_status": "official"}, "$.verification_status: invalid value"),
        ({"sources": []}, "$.sources: must be a non-empty list"),
        ({"required_documents": ["aadhaar_card"]}, "unknown document type 'aadhaar_card'"),
        ({"conditions": []}, "$.conditions: must be a non-empty list"),
        ({"benefit": {"type": "loan", "description": "x"}}, "$.benefit.type: invalid value 'loan'"),
        ({"benefit": {"type": "one_time_cash", "description": "x", "amount_inr": -5}}, "non-negative number"),
        (
            {"benefit": {"type": "one_time_cash", "description": "x", "frequency": "weekly"}},
            "invalid value 'weekly'",
        ),
        ({"unexpected": 1}, "$.unexpected: unknown key"),
        (
            {
                "relationships": [
                    {"scheme_id": "x", "relation": "LIKES", "reason": "r", "source_url": "https://a.in"}
                ]
            },
            "invalid relation 'LIKES'",
        ),
        (
            {
                "relationships": [
                    {"scheme_id": "x", "relation": "TOPS_UP", "reason": "r", "source_url": "ftp://a"}
                ]
            },
            "must be an absolute http(s) URL",
        ),
    ],
)
def test_top_level_errors(overrides, fragment):
    assert fragment in errors_for(rule_dict(**overrides))


def test_bad_source_url_and_missing_name_reported_together():
    data = rule_dict()
    data["sources"][0]["url"] = "not a url"
    del data["name"]
    msg = errors_for(data)
    assert "test.yaml: $.sources[0].url: must be an absolute http(s) URL" in msg
    assert "test.yaml: $.name: required key is missing" in msg


def test_relationship_target_must_be_loaded(tmp_path):
    data = rule_dict(
        relationships=[
            {
                "scheme_id": "ghost_scheme",
                "relation": "TOPS_UP",
                "reason": "r",
                "source_url": "https://a.gov.in/x",
            }
        ]
    )
    with pytest.raises(RuleValidationError, match="'ghost_scheme' is not a loaded scheme"):
        load_rules(write_rules(tmp_path, data))


def test_relationship_to_loaded_scheme_accepted(tmp_path):
    base = rule_dict(scheme_id="base_scheme")
    top_up = rule_dict(
        scheme_id="top_up_scheme",
        relationships=[
            {"scheme_id": "base_scheme", "relation": "TOPS_UP", "reason": "r", "source_url": "https://a.in/x"}
        ],
    )
    rules = load_rules(write_rules(tmp_path, base, top_up))
    assert [r.scheme_id for r in rules] == ["base_scheme", "top_up_scheme"]


def test_duplicate_scheme_version_rejected(tmp_path):
    with pytest.raises(RuleValidationError, match="duplicate version '2025.1'"):
        load_rules(write_rules(tmp_path, rule_dict(), rule_dict()))


def test_overlapping_versions_rejected():
    old = parse_rule(rule_dict(version="2024.1", effective_from="2024-01-01"))
    new = parse_rule(rule_dict(version="2025.1", effective_from="2025-01-01"))
    with pytest.raises(RuleValidationError, match="overlaps version '2025.1'"):
        validate_rule_set([old, new])
    closed = parse_rule(rule_dict(version="2024.1", effective_from="2024-01-01", effective_to="2024-12-31"))
    validate_rule_set([closed, new])


def test_duplicate_yaml_keys_rejected(tmp_path):
    text = yaml.safe_dump(rule_dict(), sort_keys=False) + "name: Second name\n"
    (tmp_path / "dup.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(RuleValidationError, match="duplicate key 'name'"):
        load_rules(tmp_path)


def test_yaml_syntax_error_reports_file(tmp_path):
    (tmp_path / "broken.yaml").write_text("scheme_id: [unclosed\n", encoding="utf-8")
    with pytest.raises(RuleValidationError, match="broken.yaml: YAML syntax error"):
        load_rules(tmp_path)


def test_empty_or_missing_rules_dir(tmp_path):
    with pytest.raises(RuleValidationError, match="no rule files"):
        load_rules(tmp_path)
    with pytest.raises(RuleValidationError, match="does not exist"):
        load_rules(tmp_path / "nope")

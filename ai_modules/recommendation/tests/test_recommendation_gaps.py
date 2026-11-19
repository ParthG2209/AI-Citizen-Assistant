from __future__ import annotations

from ai_modules.recommendation import ActionKind, build_recommendations, detect_gaps
from ai_modules.rule_engine.definitions import Condition, ConditionGroup, GroupMode
from ai_modules.rule_engine.fields import Provenance
from ai_modules.rule_engine.result import MissingReason, Outcome

from .factories import ELIGIBLE, POTENTIAL, evaluation, make_rule, missing, passed, result


def _rules(*rules):
    return {r.scheme_id: r for r in rules}


def test_unclaimed_excludes_enrolled_and_non_eligible():
    evals = [
        evaluation("a", ELIGIBLE, passed("c1")),
        evaluation("b", ELIGIBLE, passed("c1")),
        evaluation("c", POTENTIAL, missing("c1", "age")),
    ]
    gaps = detect_gaps(evals, ["b"], _rules(make_rule("a"), make_rule("b"), make_rule("c")))
    assert gaps.unclaimed_eligible == ("a",)
    assert gaps.potentially_eligible == ("c",)


def test_document_blocked_schemes_are_grouped_by_document():
    income = Condition(
        "income",
        "Income",
        "annual_family_income",
        "lte",
        1,
        Provenance.SELF_DECLARED,
        ("income_certificate",),
    )
    rules = _rules(make_rule("a", conditions=(income,)), make_rule("b", conditions=(income,)))
    evals = [
        evaluation("a", POTENTIAL, missing("income", "annual_family_income", ("income_certificate",))),
        evaluation(
            "b",
            POTENTIAL,
            missing("income", "annual_family_income", ("income_certificate",)),
            missing("age", "age"),
        ),
    ]
    gaps = detect_gaps(evals, [], rules)
    [gap] = gaps.document_gaps
    assert gap.doc_id == "income_certificate"
    assert [(s.scheme_id, s.condition_ids, s.fully_unlocks) for s in gap.schemes] == [
        ("a", ("income",), True),
        ("b", ("income",), False),  # 'age' is still missing after the upload
    ]
    item = gaps.missing_by_scheme["a"][0]
    assert item.kind is ActionKind.UPLOAD_DOCUMENT and item.can_self_declare
    [info] = gaps.information_needs
    assert (info.field, info.scheme_ids) == ("age", ("b",))


def test_enrolled_potential_scheme_is_not_a_gap():
    evals = [evaluation("a", POTENTIAL, missing("income", "annual_family_income", ("income_certificate",)))]
    gaps = detect_gaps(evals, ["a"], _rules(make_rule("a")))
    assert gaps.document_gaps == () and gaps.missing_by_scheme == {}


def test_insufficient_trust_classification():
    rules = _rules(
        make_rule(
            "a",
            conditions=(
                Condition(
                    "inc",
                    "Income",
                    "annual_family_income",
                    "lte",
                    1,
                    Provenance.DOCUMENT_EXTRACTED,
                    ("income_certificate",),
                ),
                Condition(
                    "land",
                    "Land",
                    "family_owns_cultivable_land",
                    "eq",
                    True,
                    Provenance.DOCUMENT_EXTRACTED,
                    ("land_record",),
                ),
                Condition(
                    "secc",
                    "SECC",
                    "listed_in_secc_or_nfsa",
                    "eq",
                    True,
                    Provenance.VERIFIED,
                    ("ration_card",),
                ),
            ),
        )
    )
    trust = MissingReason.INSUFFICIENT_TRUST
    evals = [
        evaluation(
            "a",
            POTENTIAL,
            missing(
                "inc",
                "annual_family_income",
                ("income_certificate",),
                trust,
                actual=1,
                provenance="self_declared",
            ),
            missing(
                "land",
                "family_owns_cultivable_land",
                ("land_record",),
                trust,
                actual=True,
                provenance="self_declared",
            ),
            missing(
                "secc",
                "listed_in_secc_or_nfsa",
                ("ration_card",),
                trust,
                actual=True,
                provenance="self_declared",
            ),
        )
    ]
    gaps = detect_gaps(evals, [], rules)
    kinds = {i.condition_id: i.kind for i in gaps.missing_by_scheme["a"]}
    # OCR can raise the income value to document_extracted; land records are not OCR-supported and
    # 'verified' can never come from OCR, so both need the authority.
    assert kinds == {
        "inc": ActionKind.UPLOAD_DOCUMENT,
        "land": ActionKind.OBTAIN_VERIFICATION,
        "secc": ActionKind.OBTAIN_VERIFICATION,
    }
    assert [(n.field, n.required_trust, n.documents) for n in gaps.verification_needs] == [
        ("family_owns_cultivable_land", "document_extracted", ("land_record",)),
        ("listed_in_secc_or_nfsa", "verified", ("ration_card",)),
    ]
    assert not gaps.missing_by_scheme["a"][0].can_self_declare
    report = build_recommendations(evals, [], [], rules)
    texts = [a.text for a in report.potentially_eligible[0].next_actions]
    assert any("must be verified" in t and "NFSA ration card" in t for t in texts)


def test_provide_information_lists_non_ocr_documents_as_proof():
    rules = _rules(
        make_rule(
            "a",
            conditions=(
                Condition(
                    "land", "Land", "family_owns_cultivable_land", "eq", True, resolves_with=("land_record",)
                ),
            ),
        )
    )
    evals = [evaluation("a", POTENTIAL, missing("land", "family_owns_cultivable_land", ("land_record",)))]
    gaps = detect_gaps(evals, [], rules)
    assert gaps.document_gaps == ()
    [need] = gaps.information_needs
    assert (need.kind, need.documents) == (ActionKind.PROVIDE_INFORMATION, ("land_record",))


def test_low_confidence_fields_are_listed_for_recheck():
    rules = _rules(
        make_rule(
            "a",
            conditions=(
                Condition(
                    "marks",
                    "Marks",
                    "class12_percentage",
                    "gte",
                    75,
                    Provenance.DOCUMENT_EXTRACTED,
                    ("class12_marksheet",),
                ),
            ),
        )
    )
    evals = [
        evaluation(
            "a",
            POTENTIAL,
            missing(
                "marks",
                "class12_percentage",
                ("class12_marksheet",),
                MissingReason.LOW_CONFIDENCE,
                actual=81.0,
                provenance="document_extracted",
            ),
        )
    ]
    gaps = detect_gaps(evals, [], rules)
    [low] = gaps.low_confidence_fields
    assert (low.field, low.value, low.documents) == ("class12_percentage", 81.0, ("class12_marksheet",))
    report = build_recommendations(evals, [], [], rules)
    action, apply = report.potentially_eligible[0].next_actions
    assert apply.kind == "apply" and apply.url == "https://example.org/apply"
    assert action.kind == "recheck_field" and "not accepted" in action.text


def test_any_group_is_unlocked_by_one_alternative():
    group = ConditionGroup(
        "res",
        "Residence",
        GroupMode.ANY,
        (
            Condition(
                "dom",
                "Domicile",
                "domicile_state",
                "eq",
                "Madhya Pradesh",
                resolves_with=("samagra_domicile",),
            ),
            Condition(
                "land", "Land", "family_owns_cultivable_land", "eq", True, resolves_with=("land_record",)
            ),
        ),
    )
    rules = _rules(make_rule("a", conditions=(group,)))
    children = (
        missing("dom", "domicile_state", ("samagra_domicile",)),
        missing("land", "family_owns_cultivable_land", ("land_record",)),
    )
    top = result(
        "res",
        "domicile_state,family_owns_cultivable_land",
        Outcome.MISSING,
        None,
        ("samagra_domicile", "land_record"),
        operator="any",
        children=children,
    )
    gaps = detect_gaps([evaluation("a", POTENTIAL, top)], [], rules)
    [gap] = gaps.document_gaps
    assert gap.doc_id == "samagra_domicile" and gap.schemes[0].fully_unlocks
    assert {i.any_group for i in gaps.missing_by_scheme["a"]} == {"res"}

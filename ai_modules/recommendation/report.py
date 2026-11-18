"""``build_recommendations``: turn rule-engine results + KG edges into a ranked, explained report.

Pure and deterministic: same inputs give the same report. Eligibility always comes from the
``SchemeEvaluation`` objects; the knowledge graph only suggests which schemes to look at.

Money totals never double-count:
* each scheme is counted at most once (by scheme id), however many paths reach it;
* enrolled schemes are never in the unclaimed totals;
* of two mutually exclusive schemes only the higher-ranked one is counted (the other is listed in
  ``excluded_mutually_exclusive``);
* recurring annual cash, recurring monthly cash, one-time cash and fee reimbursement are separate
  sums of published amounts only; housing, insurance cover and in-kind benefits are listed, not summed;
  schemes without a published amount are listed in ``amount_not_published``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ai_modules.knowledge_graph.models import MUTUALLY_EXCLUSIVE, SchemeEdge
from ai_modules.rule_engine.definitions import BenefitFrequency, BenefitType, SchemeRule
from ai_modules.rule_engine.fields import DOCUMENT_TYPES, PROFILE_FIELDS
from ai_modules.rule_engine.result import DISCLAIMER, EligibilityStatus, SchemeEvaluation

from .complementary import (
    ComplementaryCandidate,
    ComplementaryLink,
    ConditionSummary,
    MutualExclusionWarning,
    complementary_candidates,
    mutual_exclusion_warnings,
)
from .gap_detector import ActionKind, DocumentGap, FieldNeed, MissingItem, detect_gaps
from .ranker import RankInput, rank

NON_CASH_TYPES = (BenefitType.HOUSING_ASSISTANCE, BenefitType.INSURANCE_COVER, BenefitType.IN_KIND)


@dataclass(frozen=True, slots=True)
class NextAction:
    kind: str
    text: str
    document_id: str | None = None
    field: str | None = None
    url: str | None = None
    condition_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BenefitSummary:
    type: str
    description: str
    amount_inr: float | None
    frequency: str | None
    is_cash: bool


@dataclass(frozen=True, slots=True)
class SchemeRecommendation:
    scheme_id: str
    scheme_name: str
    status: str
    verification_status: str
    benefit: BenefitSummary
    rank: int
    rank_reasons: tuple[str, ...]
    next_actions: tuple[NextAction, ...]
    application_steps: tuple[str, ...]
    official_url: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComplementaryRecommendation:
    scheme_id: str
    scheme_name: str
    presentation: str  # available | check_eligibility
    target_status: str
    via: tuple[ComplementaryLink, ...]
    missing_conditions: tuple[ConditionSummary, ...]
    failed_conditions: tuple[ConditionSummary, ...]
    benefit: BenefitSummary | None
    rank: int
    rank_reasons: tuple[str, ...]
    next_actions: tuple[NextAction, ...]
    official_url: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TotalLine:
    scheme_id: str
    category: str
    amount_inr: float | None
    frequency: str | None
    description: str


@dataclass(frozen=True, slots=True)
class BenefitTotals:
    recurring_annual_cash_inr: float = 0.0
    recurring_monthly_cash_inr: float = 0.0
    one_time_cash_inr: float = 0.0
    fee_reimbursement_inr: float = 0.0
    non_cash: tuple[TotalLine, ...] = ()
    amount_not_published: tuple[str, ...] = ()
    not_summed: tuple[TotalLine, ...] = ()  # published amount but no summable frequency
    excluded_mutually_exclusive: tuple[tuple[str, str], ...] = ()  # (excluded, conflicts_with)
    counted_scheme_ids: tuple[str, ...] = ()
    lines: tuple[TotalLine, ...] = ()


@dataclass(frozen=True, slots=True)
class RecommendationReport:
    unclaimed: tuple[SchemeRecommendation, ...]
    potentially_eligible: tuple[SchemeRecommendation, ...]
    complementary: tuple[ComplementaryRecommendation, ...]
    document_gaps: tuple[DocumentGap, ...]
    information_needs: tuple[FieldNeed, ...]
    verification_needs: tuple[FieldNeed, ...]
    low_confidence_fields: tuple[FieldNeed, ...]
    warnings: tuple[MutualExclusionWarning, ...]
    enrolled_scheme_ids: tuple[str, ...]
    unclaimed_totals: BenefitTotals
    potential_totals: BenefitTotals
    disclaimer: str = DISCLAIMER
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_recommendations(
    evaluations: Sequence[SchemeEvaluation],
    enrolled_scheme_ids: Iterable[str],
    complements: Sequence[SchemeEdge],
    rules: Mapping[str, SchemeRule] | Sequence[SchemeRule],
) -> RecommendationReport:
    """Build the report.

    ``complements`` should come from ``KnowledgeGraph.complementary_schemes`` queried with every
    evaluated and enrolled scheme id, so top-up and mutual-exclusion edges in both directions are seen.
    """
    rule_map = rules if isinstance(rules, Mapping) else {r.scheme_id: r for r in rules}
    enrolled = tuple(sorted(set(enrolled_scheme_ids)))
    by_id = {e.scheme_id: e for e in evaluations}
    names = {sid: r.name for sid, r in rule_map.items()} | {e.scheme_id: e.scheme_name for e in evaluations}

    gaps = detect_gaps(evaluations, enrolled, rule_map)
    warnings = mutual_exclusion_warnings(evaluations, enrolled, complements, names)
    warning_text = _warnings_by_scheme(warnings)
    exclusive = _exclusive_pairs(complements)

    def scheme_rec(scheme_id: str, position: int, reasons: tuple[str, ...]) -> SchemeRecommendation:
        evaluation, rule = by_id[scheme_id], rule_map.get(scheme_id)
        missing = gaps.missing_by_scheme.get(scheme_id, ())
        return SchemeRecommendation(
            scheme_id=scheme_id,
            scheme_name=evaluation.scheme_name,
            status=str(evaluation.status),
            verification_status=evaluation.verification_status,
            benefit=_benefit(rule),
            rank=position,
            rank_reasons=reasons,
            next_actions=_actions(evaluation.status, missing, rule),
            application_steps=rule.application_steps if rule else (),
            official_url=_official_url(rule),
            warnings=warning_text.get(scheme_id, ()),
        )

    def rank_inputs(scheme_ids: Iterable[str]) -> list[RankInput]:
        return [
            RankInput(
                sid,
                str(by_id[sid].status),
                rule_map[sid].benefit,
                len(gaps.missing_by_scheme.get(sid, ())),
                by_id[sid].verification_status,
            )
            for sid in scheme_ids
            if sid in rule_map
        ]

    unclaimed_ranked = rank(rank_inputs(gaps.unclaimed_eligible))
    potential_ranked = rank(rank_inputs(gaps.potentially_eligible))
    unclaimed = tuple(scheme_rec(r.item.scheme_id, r.rank, r.reasons) for r in unclaimed_ranked)
    potential = tuple(scheme_rec(r.item.scheme_id, r.rank, r.reasons) for r in potential_ranked)

    candidates = complementary_candidates(evaluations, enrolled, complements, names)
    complementary = _complementary(candidates, by_id, rule_map, gaps.missing_by_scheme, names, warning_text)

    counted_unclaimed = [r.item.scheme_id for r in unclaimed_ranked]
    unclaimed_totals = _totals(counted_unclaimed, rule_map, exclusive, already=set(enrolled))
    potential_totals = _totals(
        [r.item.scheme_id for r in potential_ranked],
        rule_map,
        exclusive,
        already=set(enrolled) | set(unclaimed_totals.counted_scheme_ids),
    )
    notes = tuple(
        f"No rule definition loaded for evaluated scheme '{e.scheme_id}'; it is not ranked or totalled."
        for e in evaluations
        if e.scheme_id not in rule_map
    )
    return RecommendationReport(
        unclaimed=unclaimed,
        potentially_eligible=potential,
        complementary=complementary,
        document_gaps=gaps.document_gaps,
        information_needs=gaps.information_needs,
        verification_needs=gaps.verification_needs,
        low_confidence_fields=gaps.low_confidence_fields,
        warnings=tuple(warnings),
        enrolled_scheme_ids=enrolled,
        unclaimed_totals=unclaimed_totals,
        potential_totals=potential_totals,
        notes=notes,
    )


def _complementary(
    candidates: Sequence[ComplementaryCandidate],
    by_id: Mapping[str, SchemeEvaluation],
    rules: Mapping[str, SchemeRule],
    missing_by_scheme: Mapping[str, tuple[MissingItem, ...]],
    names: Mapping[str, str],
    warning_text: Mapping[str, tuple[str, ...]],
) -> tuple[ComplementaryRecommendation, ...]:
    inputs = {
        c.scheme_id: RankInput(
            c.scheme_id,
            c.target_status,
            rules[c.scheme_id].benefit,
            len(c.missing_conditions) + len(c.failed_conditions),
            by_id[c.scheme_id].verification_status
            if c.scheme_id in by_id
            else str(rules[c.scheme_id].verification_status),
        )
        for c in candidates
        if c.scheme_id in rules
    }
    order = {r.item.scheme_id: r for r in rank(list(inputs.values()))}
    ranked = sorted(
        candidates,
        key=lambda c: (order[c.scheme_id].rank if c.scheme_id in order else len(order) + 1, c.scheme_id),
    )
    result = []
    for position, c in enumerate(ranked, start=1):
        rule = rules.get(c.scheme_id)
        evaluation = by_id.get(c.scheme_id)
        if evaluation is None:
            url = _official_url(rule)
            actions: tuple[NextAction, ...] = (
                NextAction(
                    ActionKind.APPLY.value,
                    "This scheme was not evaluated for the requested date; check its eligibility "
                    f"with the official source{f': {url}' if url else ''}.",
                    url=url,
                ),
            )
        elif evaluation.status is EligibilityStatus.NOT_ELIGIBLE:
            actions = ()
        else:
            actions = _actions(evaluation.status, missing_by_scheme.get(c.scheme_id, ()), rule)
        result.append(
            ComplementaryRecommendation(
                scheme_id=c.scheme_id,
                scheme_name=names.get(c.scheme_id, c.scheme_id),
                presentation=c.presentation.value,
                target_status=c.target_status,
                via=c.via,
                missing_conditions=c.missing_conditions,
                failed_conditions=c.failed_conditions,
                benefit=_benefit(rule) if rule else None,
                rank=position,
                rank_reasons=order[c.scheme_id].reasons if c.scheme_id in order else (),
                next_actions=actions,
                official_url=_official_url(rule),
                warnings=warning_text.get(c.scheme_id, ()),
            )
        )
    return tuple(result)


def _benefit(rule: SchemeRule | None) -> BenefitSummary:
    if rule is None:
        return BenefitSummary("unknown", "No rule definition loaded", None, None, False)
    b = rule.benefit
    return BenefitSummary(
        str(b.type), b.description, b.amount_inr, str(b.frequency) if b.frequency else None, b.is_cash
    )


def _official_url(rule: SchemeRule | None) -> str | None:
    return rule.sources[0].url if rule and rule.sources else None


def _field_label(name: str) -> str:
    spec = PROFILE_FIELDS.get(name)
    return spec.label if spec else name


def _doc_labels(doc_ids: Iterable[str]) -> str:
    return ", ".join(DOCUMENT_TYPES[d].label for d in doc_ids if d in DOCUMENT_TYPES)


def _actions(
    status: EligibilityStatus, missing: Sequence[MissingItem], rule: SchemeRule | None
) -> tuple[NextAction, ...]:
    """Deterministic next steps built only from rule data and the missing-item classification."""
    url = _official_url(rule)
    title = rule.sources[0].title if rule and rule.sources else "the official scheme source"
    channel = f"the official channel described in {title}" + (f" ({url})." if url else ".")
    apply = NextAction(ActionKind.APPLY.value, f"Apply through {channel}", url=url)
    if status is EligibilityStatus.ELIGIBLE:
        return (apply,)

    actions: list[NextAction] = []
    uploads: dict[str, list[MissingItem]] = {}
    for item in missing:
        if item.kind is ActionKind.UPLOAD_DOCUMENT:
            for doc_id in item.documents:
                uploads.setdefault(doc_id, []).append(item)
    for doc_id, items in sorted(uploads.items()):
        fields = ", ".join(dict.fromkeys(_field_label(i.field) for i in items))
        purposes = "; ".join(dict.fromkeys(i.description for i in items))
        alt = " (or enter the value yourself)" if all(i.can_self_declare for i in items) else ""
        actions.append(
            NextAction(
                ActionKind.UPLOAD_DOCUMENT.value,
                f"Upload your {DOCUMENT_TYPES[doc_id].label}{alt} so the assistant can read {fields}. "
                f"Needed for: {purposes}.",
                document_id=doc_id,
                condition_ids=tuple(i.condition_id for i in items),
            )
        )

    for item in missing:
        label = _field_label(item.field)
        if item.kind is ActionKind.PROVIDE_INFORMATION:
            proof = f" Keep your {_doc_labels(item.documents)} ready as proof." if item.documents else ""
            text = f"Add '{label}' to your profile. Needed for: {item.description}.{proof}"
        elif item.kind is ActionKind.OBTAIN_VERIFICATION:
            needed = "verified" if item.required_trust == "verified" else "document-backed"
            current = item.provenance.replace("_", " ") if item.provenance else "not provided"
            present = (
                f"Present your {_doc_labels(item.documents)} to the sanctioning authority"
                if item.documents
                else "Have it confirmed by the sanctioning authority"
            )
            text = (
                f"'{label}' must be {needed} for this condition (current value: {current}). "
                f"{present} during the official application" + (f" ({url})." if url else ".")
            )
        elif item.kind is ActionKind.RECHECK_FIELD:
            clearer = f"upload a clearer copy of your {_doc_labels(item.documents)}" if item.documents else ""
            if item.required_trust in (None, "self_declared"):
                fix = "confirm or correct it" + (f", or {clearer}" if clearer else "")
            else:
                fix = (clearer or "provide a clearer document") + (
                    "; a typed correction counts as self-declared and is not accepted for this condition"
                )
            text = f"Re-check '{label}': the value read from your document has low OCR confidence; {fix}."
        else:
            continue
        actions.append(
            NextAction(
                item.kind.value,
                text,
                document_id=item.documents[0]
                if item.kind is ActionKind.RECHECK_FIELD and item.documents
                else None,
                field=item.field,
                url=url if item.kind is ActionKind.OBTAIN_VERIFICATION else None,
                condition_ids=(item.condition_id,),
            )
        )
    actions.append(replace(apply, text=f"After resolving the items above, apply through {channel}"))
    return tuple(actions)


def _warnings_by_scheme(warnings: Sequence[MutualExclusionWarning]) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for w in warnings:
        for sid in w.scheme_ids:
            found.setdefault(sid, []).append(w.text)
    return {sid: tuple(texts) for sid, texts in found.items()}


def _exclusive_pairs(complements: Sequence[SchemeEdge]) -> dict[str, set[str]]:
    pairs: dict[str, set[str]] = {}
    for edge in complements:
        if edge.relation == MUTUALLY_EXCLUSIVE:
            pairs.setdefault(edge.from_scheme, set()).add(edge.to_scheme)
            pairs.setdefault(edge.to_scheme, set()).add(edge.from_scheme)
    return pairs


def _category(rule: SchemeRule) -> str:
    b = rule.benefit
    if b.type in NON_CASH_TYPES:
        return "non_cash"
    if b.type is BenefitType.ONE_TIME_CASH:
        return "one_time_cash"
    if b.type is BenefitType.FEE_REIMBURSEMENT:
        return "fee_reimbursement"
    if b.frequency is BenefitFrequency.ANNUAL:
        return "recurring_annual_cash"
    if b.frequency is BenefitFrequency.MONTHLY:
        return "recurring_monthly_cash"
    return "recurring_cash_unspecified_frequency"


def _totals(
    ranked_ids: Sequence[str],
    rules: Mapping[str, SchemeRule],
    exclusive: Mapping[str, set[str]],
    already: set[str],
) -> BenefitTotals:
    """Totals over ``ranked_ids`` (best first). ``already`` = schemes held or counted elsewhere."""
    sums = {
        "recurring_annual_cash": 0.0,
        "recurring_monthly_cash": 0.0,
        "one_time_cash": 0.0,
        "fee_reimbursement": 0.0,
    }
    counted: list[str] = []
    lines: list[TotalLine] = []
    non_cash: list[TotalLine] = []
    not_published: list[str] = []
    not_summed: list[TotalLine] = []
    excluded: list[tuple[str, str]] = []
    for sid in dict.fromkeys(ranked_ids):
        rule = rules[sid]
        conflict = sorted(exclusive.get(sid, set()) & (already | set(counted)))
        if conflict:
            excluded.append((sid, conflict[0]))
            continue
        counted.append(sid)
        b = rule.benefit
        category = _category(rule)
        line = TotalLine(
            sid, category, b.amount_inr, str(b.frequency) if b.frequency else None, b.description
        )
        lines.append(line)
        if category == "non_cash":
            non_cash.append(line)
        elif b.amount_inr is None:
            not_published.append(sid)
        elif category in sums:
            sums[category] += b.amount_inr
        else:
            not_summed.append(line)
    return BenefitTotals(
        recurring_annual_cash_inr=sums["recurring_annual_cash"],
        recurring_monthly_cash_inr=sums["recurring_monthly_cash"],
        one_time_cash_inr=sums["one_time_cash"],
        fee_reimbursement_inr=sums["fee_reimbursement"],
        non_cash=tuple(non_cash),
        amount_not_published=tuple(sorted(not_published)),
        not_summed=tuple(not_summed),
        excluded_mutually_exclusive=tuple(excluded),
        counted_scheme_ids=tuple(sorted(counted)),
        lines=tuple(lines),
    )

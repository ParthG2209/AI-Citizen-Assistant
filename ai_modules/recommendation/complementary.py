"""Complementary / top-up / follow-on scheme suggestions from knowledge-graph edges.

Edge semantics (as declared in rule YAML, see ``knowledge_graph.models``):
    (A)-[COMPLEMENTED_BY]->(B)    A anchors, suggest B
    (A)-[PREREQUISITE_FOR]->(B)   A anchors, suggest B
    (A)-[TOPS_UP]->(B)            A is the top-up of B: B anchors, suggest A
    (A)-[MUTUALLY_EXCLUSIVE_WITH]->(B)  symmetric; only ever a warning

An anchor is a scheme the citizen is enrolled in or that evaluates ``eligible``. A suggested scheme is
presented as ``available`` only when its OWN evaluation is eligible; otherwise ``check_eligibility``
with its missing / failed conditions. The edge never changes eligibility.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from ai_modules.knowledge_graph.models import MUTUALLY_EXCLUSIVE, SchemeEdge
from ai_modules.rule_engine.result import EligibilityStatus, SchemeEvaluation


class Presentation(StrEnum):
    AVAILABLE = "available"
    CHECK_ELIGIBILITY = "check_eligibility"


@dataclass(frozen=True, slots=True)
class ConditionSummary:
    condition_id: str
    description: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ComplementaryLink:
    anchor_scheme: str
    relation: str
    reason: str
    source_url: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ComplementaryCandidate:
    scheme_id: str
    presentation: Presentation
    target_status: str  # eligible | potentially_eligible | not_eligible | not_evaluated
    via: tuple[ComplementaryLink, ...]
    missing_conditions: tuple[ConditionSummary, ...]
    failed_conditions: tuple[ConditionSummary, ...]


@dataclass(frozen=True, slots=True)
class MutualExclusionWarning:
    scheme_ids: tuple[str, str]
    reason: str
    source_url: str | None
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def orient(edge: SchemeEdge) -> tuple[str, str] | None:
    """(anchor, suggested) for a suggestion edge; None for mutual exclusion."""
    if edge.relation == MUTUALLY_EXCLUSIVE:
        return None
    if edge.relation == "TOPS_UP":
        return edge.to_scheme, edge.from_scheme
    return edge.from_scheme, edge.to_scheme


def _link_text(edge: SchemeEdge, names: Mapping[str, str]) -> str:
    a, b = names.get(edge.from_scheme, edge.from_scheme), names.get(edge.to_scheme, edge.to_scheme)
    verb = {
        "COMPLEMENTED_BY": "is complemented by",
        "TOPS_UP": "tops up",
        "PREREQUISITE_FOR": "is a prerequisite for",
    }.get(edge.relation, edge.relation)
    return f"{a} {verb} {b}: {edge.reason}"


def complementary_candidates(
    evaluations: Sequence[SchemeEvaluation],
    enrolled_scheme_ids: Iterable[str],
    complements: Sequence[SchemeEdge],
    names: Mapping[str, str],
) -> list[ComplementaryCandidate]:
    """One candidate per suggested scheme (merged over all edges reaching it), sorted by scheme id."""
    by_id = {e.scheme_id: e for e in evaluations}
    enrolled = set(enrolled_scheme_ids)
    anchors = enrolled | {e.scheme_id for e in evaluations if e.status is EligibilityStatus.ELIGIBLE}
    links: dict[str, list[ComplementaryLink]] = {}
    for edge in complements:
        oriented = orient(edge)
        if oriented is None:
            continue
        anchor, target = oriented
        if anchor not in anchors or target in enrolled or target == anchor:
            continue
        links.setdefault(target, []).append(
            ComplementaryLink(anchor, edge.relation, edge.reason, edge.source_url, _link_text(edge, names))
        )

    candidates = []
    for target in sorted(links):
        evaluation = by_id.get(target)
        status = str(evaluation.status) if evaluation else "not_evaluated"
        via = tuple(sorted(set(links[target]), key=lambda link: (link.anchor_scheme, link.relation)))
        candidates.append(
            ComplementaryCandidate(
                scheme_id=target,
                presentation=Presentation.AVAILABLE
                if status == EligibilityStatus.ELIGIBLE
                else Presentation.CHECK_ELIGIBILITY,
                target_status=status,
                via=via,
                missing_conditions=_summaries(evaluation, "missing"),
                failed_conditions=_summaries(evaluation, "failed"),
            )
        )
    return candidates


def _summaries(evaluation: SchemeEvaluation | None, which: str) -> tuple[ConditionSummary, ...]:
    if evaluation is None:
        return ()
    results = evaluation.missing if which == "missing" else evaluation.failed
    return tuple(ConditionSummary(r.condition_id, r.description, r.message) for r in results)


def mutual_exclusion_warnings(
    evaluations: Sequence[SchemeEvaluation],
    enrolled_scheme_ids: Iterable[str],
    complements: Sequence[SchemeEdge],
    names: Mapping[str, str],
) -> list[MutualExclusionWarning]:
    """Warn when both schemes of a MUTUALLY_EXCLUSIVE_WITH pair are enrolled, eligible or potentially
    eligible. Pairs where either side is not eligible are irrelevant and skipped."""
    in_play = set(enrolled_scheme_ids) | {
        e.scheme_id
        for e in evaluations
        if e.status in (EligibilityStatus.ELIGIBLE, EligibilityStatus.POTENTIALLY_ELIGIBLE)
    }
    warnings: dict[tuple[str, str], MutualExclusionWarning] = {}
    for edge in sorted(complements, key=lambda e: (e.from_scheme, e.to_scheme, e.reason)):
        if edge.relation != MUTUALLY_EXCLUSIVE:
            continue
        pair = tuple(sorted((edge.from_scheme, edge.to_scheme)))
        if pair[0] not in in_play or pair[1] not in in_play or pair in warnings:
            continue
        a, b = (names.get(s, s) for s in pair)
        warnings[pair] = MutualExclusionWarning(
            scheme_ids=(pair[0], pair[1]),
            reason=edge.reason,
            source_url=edge.source_url,
            text=f"{a} and {b} cannot both be availed: {edge.reason}",
        )
    return [warnings[pair] for pair in sorted(warnings)]

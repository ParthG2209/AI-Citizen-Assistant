"""Deterministic under-served area detection on top of the coverage aggregates.

An area (district / block / village) is **under-served** when ALL of the following hold:

1. ``coverage_pct`` is known (areas with no eligible population are never flagged; they carry
   ``coverage_flag = "no_eligible_population"`` instead);
2. ``coverage_pct < max_coverage_pct`` (default 50.0); and
3. ``gap >= min_gap[level]`` (eligible-but-not-enrolled count; defaults district 500, block 100, village 20),
   so that tiny areas with one or two missing enrolments are not flagged.

Thresholds come from ``UnderservedCriteria`` (env: ``UNDERSERVED_MAX_COVERAGE_PCT``,
``UNDERSERVED_MIN_GAP_DISTRICT``, ``UNDERSERVED_MIN_GAP_BLOCK``, ``UNDERSERVED_MIN_GAP_VILLAGE``,
``ANALYTICS_TOP_DOCUMENTS``). The default thresholds are demo settings for the synthetic dataset, not
government targets.

Top missing documents per area: document types ranked by distinct citizens (eligible or potentially
eligible, not enrolled) who lack them; ties broken by document id; at most ``top_documents`` per area.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from ai_modules.analytics.reference import document_label

if TYPE_CHECKING:
    import pandas as pd

    from ai_modules.analytics.coverage_job import CoverageResult

LEVEL_TABLES = {"district": "districts", "block": "blocks", "village": "villages"}


@dataclass(frozen=True, slots=True)
class UnderservedCriteria:
    max_coverage_pct: float = 50.0
    min_gap_district: int = 500
    min_gap_block: int = 100
    min_gap_village: int = 20
    top_documents: int = 3

    @classmethod
    def from_env(cls) -> UnderservedCriteria:
        env, default = os.environ, cls()
        return cls(
            max_coverage_pct=float(env.get("UNDERSERVED_MAX_COVERAGE_PCT", default.max_coverage_pct)),
            min_gap_district=int(env.get("UNDERSERVED_MIN_GAP_DISTRICT", default.min_gap_district)),
            min_gap_block=int(env.get("UNDERSERVED_MIN_GAP_BLOCK", default.min_gap_block)),
            min_gap_village=int(env.get("UNDERSERVED_MIN_GAP_VILLAGE", default.min_gap_village)),
            top_documents=int(env.get("ANALYTICS_TOP_DOCUMENTS", default.top_documents)),
        )

    def min_gap(self, level: str) -> int:
        return {
            "district": self.min_gap_district,
            "block": self.min_gap_block,
            "village": self.min_gap_village,
        }[level]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Detection:
    criteria: UnderservedCriteria
    # "districts" / "blocks" / "villages" -> coverage table plus an ``underserved`` bool column
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    # level ("district" | "block" | "village" | "scheme") -> area id -> [{document_type, label, citizens}]
    top_missing_documents: dict[str, dict[str, list[dict]]] = field(default_factory=dict)

    def underserved_ids(self, level: str) -> list[str]:
        table = self.tables[LEVEL_TABLES[level]]
        id_column = f"{level}_id"
        return table.loc[table["underserved"], id_column].tolist()


def flag_underserved(table: pd.DataFrame, max_coverage_pct: float, min_gap: int) -> pd.Series:
    """Boolean Series: known coverage below ``max_coverage_pct`` and gap of at least ``min_gap``."""
    coverage = table["coverage_pct"]
    return (coverage.notna() & (coverage < max_coverage_pct) & (table["gap"] >= min_gap)).astype(bool)


def top_missing_documents(missing: pd.DataFrame, level: str, limit: int) -> dict[str, list[dict]]:
    rows = missing[missing["level"] == level].sort_values(
        ["area_id", "citizens", "document_type"], ascending=[True, False, True], kind="mergesort"
    )
    top: dict[str, list[dict]] = {}
    for area_id, group in rows.groupby("area_id", sort=True):
        top[str(area_id)] = [
            {"document_type": doc, "label": document_label(doc), "citizens": int(citizens)}
            for doc, citizens in zip(
                group["document_type"].head(limit), group["citizens"].head(limit), strict=True
            )
        ]
    return top


def detect_underserved(result: CoverageResult, criteria: UnderservedCriteria | None = None) -> Detection:
    criteria = criteria or UnderservedCriteria.from_env()
    detection = Detection(criteria)
    for level, name in LEVEL_TABLES.items():
        table = result[name].copy()
        table["underserved"] = flag_underserved(table, criteria.max_coverage_pct, criteria.min_gap(level))
        detection.tables[name] = table
    for level in ("district", "block", "village", "scheme"):
        detection.top_missing_documents[level] = top_missing_documents(
            result["missing_documents"], level, criteria.top_documents
        )
    return detection

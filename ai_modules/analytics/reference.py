"""Reference data shared by the analytics jobs: real MP district geography, scheme catalog, document types.

Everything here is lightweight (stdlib + the rule-engine contract modules); no pandas/Spark imports.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ai_modules.rule_engine.fields import DOCUMENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GEOJSON = REPO_ROOT / "data" / "geo" / "mp_districts.geojson"


@dataclass(frozen=True, slots=True)
class District:
    """A real Madhya Pradesh district (from data/geo/mp_districts.geojson)."""

    district_id: str
    name: str
    lat: float
    lng: float
    formed_after_2011: bool


@dataclass(frozen=True, slots=True)
class SchemeInfo:
    """The slice of a scheme rule the analytics need: id, display name and required document ids."""

    scheme_id: str
    name: str
    required_documents: tuple[str, ...] = ()


def load_districts(geojson_path: Path = DEFAULT_GEOJSON) -> dict[str, District]:
    """Read district ids, names and centroids from the GeoJSON, keyed by ``district_id``."""
    with Path(geojson_path).open(encoding="utf-8") as fh:
        collection = json.load(fh)
    districts: dict[str, District] = {}
    for feature in collection["features"]:
        props = feature["properties"]
        lat, lng = props["centroid"]
        districts[props["district_id"]] = District(
            district_id=props["district_id"],
            name=props["name"],
            lat=float(lat),
            lng=float(lng),
            formed_after_2011=bool(props["formed_after_2011"]),
        )
    return districts


def load_scheme_catalog(rules_dir: Path | None = None) -> dict[str, SchemeInfo]:
    """Scheme ids/names/required documents from the deterministic rule engine's YAML rules."""
    from ai_modules.rule_engine import RuleEngine

    return schemes_from_rules(RuleEngine(rules_dir).schemes())


def schemes_from_rules(rules: Iterable) -> dict[str, SchemeInfo]:
    """Convert rule-engine ``SchemeRule`` objects to ``SchemeInfo`` (sorted by scheme id)."""
    infos = [SchemeInfo(rule.scheme_id, rule.name, tuple(rule.required_documents)) for rule in rules]
    return {info.scheme_id: info for info in sorted(infos, key=lambda info: info.scheme_id)}


def document_label(document_type: str) -> str:
    doc = DOCUMENT_TYPES.get(document_type)
    return doc.label if doc else document_type


KNOWN_DOCUMENT_TYPES: frozenset[str] = frozenset(DOCUMENT_TYPES)

"""Builders for synthetic rule definitions and profiles used by the rule-engine tests."""

from __future__ import annotations

import copy
from typing import Any

from ai_modules.rule_engine import FieldValue, Provenance

BASE_RULE: dict[str, Any] = {
    "scheme_id": "test_scheme",
    "version": "2025.1",
    "name": "Test Scheme",
    "level": "state",
    "state": "Madhya Pradesh",
    "department": "Test Department",
    "effective_from": "2025-01-01",
    "verification_status": "unverified",
    "sources": [
        {
            "title": "Test guidelines",
            "url": "https://example.gov.in/guidelines.pdf",
            "publisher": "Test Department",
            "accessed_on": "2026-11-18",
            "section": "Para 1",
        }
    ],
    "benefit": {
        "type": "one_time_cash",
        "description": "Synthetic test benefit",
        "amount_inr": 1000,
        "frequency": "one_time",
    },
    "required_documents": ["income_certificate"],
    "conditions": [
        {
            "id": "income_limit",
            "description": "Annual family income at most INR 6,00,000",
            "field": "annual_family_income",
            "operator": "lte",
            "value": 600000,
            "resolves_with": ["income_certificate"],
            "source_ref": "Para 1",
        }
    ],
}


def rule_dict(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE_RULE)
    data.update(copy.deepcopy(overrides))
    return data


def leaf(cid: str, field: str, operator: str, value: Any = None, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": cid,
        "description": f"{field} {operator} {value}",
        "field": field,
        "operator": operator,
        "source_ref": "Para 1",
    }
    if value is not None:
        item["value"] = value
    item.update(extra)
    return item


def declared(value: Any) -> FieldValue:
    return FieldValue(value, Provenance.SELF_DECLARED)


def extracted(value: Any, confidence: float | None = 0.95) -> FieldValue:
    return FieldValue(value, Provenance.DOCUMENT_EXTRACTED, confidence=confidence, source_document_id="doc-1")


def verified(value: Any) -> FieldValue:
    return FieldValue(value, Provenance.VERIFIED)

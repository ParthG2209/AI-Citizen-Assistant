"""Load scheme rule YAML files into validated, immutable ``SchemeRule`` objects.

Validation is strict: unknown keys, unknown profile fields, unknown operators, operator/field type
mismatches, invalid enum choices, bad dates or URLs, duplicate condition ids, duplicate
(scheme_id, version) pairs, overlapping version periods and relationships pointing at schemes that
are not in the loaded set are all rejected. Every problem is reported as ``<file>: <path>: <message>``
and all problems found are raised together in one ``RuleValidationError``.

The grammar is documented in ``docs/eligibility_rules.md``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .definitions import (
    Benefit,
    BenefitFrequency,
    BenefitType,
    Condition,
    ConditionGroup,
    GroupMode,
    Relationship,
    SchemeLevel,
    SchemeRule,
    Source,
    VerificationStatus,
)
from .fields import DOCUMENT_TYPES, PROFILE_FIELDS, FieldSpec, FieldType, Provenance, coerce_value
from .operators import OPERATORS, ValueShape

DEFAULT_RULES_DIR = Path(__file__).resolve().parent / "rules"

RELATION_TYPES = ("COMPLEMENTED_BY", "TOPS_UP", "PREREQUISITE_FOR", "MUTUALLY_EXCLUSIVE_WITH")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")

_TOP_REQUIRED = (
    "scheme_id",
    "version",
    "name",
    "level",
    "department",
    "effective_from",
    "verification_status",
    "sources",
    "benefit",
    "conditions",
)
_TOP_OPTIONAL = (
    "state",
    "effective_to",
    "required_documents",
    "relationships",
    "limitations",
    "application_steps",
)
_SOURCE_KEYS = ("title", "url", "publisher", "accessed_on", "section")
_BENEFIT_KEYS = ("type", "description", "amount_inr", "frequency", "is_cash")
_LEAF_KEYS = (
    "id",
    "description",
    "field",
    "operator",
    "value",
    "min_trust",
    "resolves_with",
    "failure_message",
    "source_ref",
)
_GROUP_KEYS = ("id", "description", "all", "any", "failure_message", "source_ref")
_RELATION_KEYS = ("scheme_id", "relation", "reason", "source_url")


class RuleValidationError(ValueError):
    """Raised when one or more rule definitions are invalid. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("Invalid scheme rule definition(s):\n" + "\n".join(f"- {e}" for e in self.errors))


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last one."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key '{key}'", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


# --------------------------------------------------------------------------- canonical form / hashing


def to_canonical(obj: Any) -> Any:
    """Convert rule/result objects to plain JSON-compatible data (enums -> values, dates -> ISO)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_canonical(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): to_canonical(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [to_canonical(v) for v in obj]
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)  # 600000 and 600000.0 hash identically
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(to_canonical(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_rule_hash(rule: SchemeRule) -> str:
    """sha256 of the canonical JSON of the parsed definition (excluding ``rule_hash`` itself)."""
    data = to_canonical(rule)
    data.pop("rule_hash", None)
    return sha256_hex(canonical_json(data))


# --------------------------------------------------------------------------- parsing


class _Parser:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.errors: list[str] = []
        self.condition_ids: set[str] = set()

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{self.origin}: {path}: {message}")

    # ---- generic helpers

    def keys(self, data: Any, path: str, required: Iterable[str], allowed: Iterable[str]) -> bool:
        if not isinstance(data, Mapping):
            self.error(path, f"expected a mapping, got {type(data).__name__}")
            return False
        allowed_set = set(allowed)
        for key in data:
            if key not in allowed_set:
                self.error(f"{path}.{key}", f"unknown key (allowed: {', '.join(sorted(allowed_set))})")
        ok = True
        for key in required:
            if key not in data or data[key] is None:
                self.error(f"{path}.{key}", "required key is missing")
                ok = False
        return ok

    def text(self, data: Mapping[str, Any], key: str, path: str) -> str | None:
        """Optional/required text value; absence of required keys is reported by ``keys()``."""
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            self.error(f"{path}.{key}", f"must be a non-empty string, got {value!r}")
            return None
        return value.strip()

    def date_value(self, data: Mapping[str, Any], key: str, path: str) -> date | None:
        value = data.get(key)
        if value is None:
            return None
        if isinstance(value, datetime):
            self.error(f"{path}.{key}", "must be a date (YYYY-MM-DD), not a timestamp")
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        self.error(f"{path}.{key}", f"must be a date in YYYY-MM-DD format, got {value!r}")
        return None

    def url(self, value: Any, path: str) -> str | None:
        if not isinstance(value, str):
            self.error(path, f"must be a URL string, got {value!r}")
            return None
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in value:
            self.error(path, f"must be an absolute http(s) URL, got {value!r}")
            return None
        return value

    def enum(self, enum_cls: type[Enum], value: Any, path: str) -> Any:
        try:
            return enum_cls(value)
        except ValueError:
            choices = ", ".join(str(m.value) for m in enum_cls)  # type: ignore[attr-defined]
            self.error(path, f"invalid value {value!r} (allowed: {choices})")
            return None

    def text_list(self, data: Mapping[str, Any], key: str, path: str) -> tuple[str, ...]:
        value = data.get(key)
        if value is None:
            return ()
        if not isinstance(value, list):
            self.error(f"{path}.{key}", "must be a list of strings")
            return ()
        items: list[str] = []
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                self.error(f"{path}.{key}[{i}]", f"must be a non-empty string, got {item!r}")
            else:
                items.append(item.strip())
        return tuple(items)

    # ---- sections

    def rule(self, data: Any) -> SchemeRule | None:
        if not self.keys(data, "$", _TOP_REQUIRED, _TOP_REQUIRED + _TOP_OPTIONAL):
            if not isinstance(data, Mapping):
                return None
        scheme_id = data.get("scheme_id")
        if not isinstance(scheme_id, str) or not _ID_RE.match(scheme_id):
            self.error("$.scheme_id", f"must be snake_case ([a-z][a-z0-9_]*), got {scheme_id!r}")
        version = data.get("version")
        if not isinstance(version, str) or not _VERSION_RE.match(version):
            self.error("$.version", f"must be a quoted version string such as '2025.1', got {version!r}")
        name = self.text(data, "name", "$")
        department = self.text(data, "department", "$")
        level = self.enum(SchemeLevel, data.get("level"), "$.level")
        state = self.text(data, "state", "$")
        if level is SchemeLevel.STATE and state is None:
            self.error("$.state", "required when level is 'state'")
        effective_from = self.date_value(data, "effective_from", "$")
        effective_to = self.date_value(data, "effective_to", "$")
        if effective_from and effective_to and effective_to < effective_from:
            self.error("$.effective_to", f"{effective_to} is before effective_from {effective_from}")
        status = self.enum(VerificationStatus, data.get("verification_status"), "$.verification_status")
        sources = self.sources(data.get("sources"))
        benefit = self.benefit(data.get("benefit"))
        documents = self.documents(data.get("required_documents"))
        conditions = self.conditions(data.get("conditions"), "$.conditions")
        relationships = self.relationships(data.get("relationships"))
        limitations = self.text_list(data, "limitations", "$")
        steps = self.text_list(data, "application_steps", "$")
        if self.errors:
            return None
        rule = SchemeRule(
            scheme_id=scheme_id,
            version=version,
            name=name,
            level=level,
            department=department,
            effective_from=effective_from,
            verification_status=status,
            benefit=benefit,
            conditions=conditions,
            sources=sources,
            state=state,
            effective_to=effective_to,
            required_documents=documents,
            relationships=relationships,
            limitations=limitations,
            application_steps=steps,
        )
        return dataclasses.replace(rule, rule_hash=compute_rule_hash(rule))

    def sources(self, value: Any) -> tuple[Source, ...]:
        if not isinstance(value, list) or not value:
            self.error("$.sources", "must be a non-empty list of official sources")
            return ()
        result: list[Source] = []
        for i, item in enumerate(value):
            path = f"$.sources[{i}]"
            if not self.keys(item, path, ("title", "url", "publisher", "accessed_on"), _SOURCE_KEYS):
                continue
            url = self.url(item.get("url"), f"{path}.url")
            title = self.text(item, "title", path)
            publisher = self.text(item, "publisher", path)
            accessed = self.date_value(item, "accessed_on", path)
            section = self.text(item, "section", path)
            if url and title and publisher and accessed:
                result.append(Source(title, url, publisher, accessed, section))
        return tuple(result)

    def benefit(self, value: Any) -> Benefit | None:
        path = "$.benefit"
        if not self.keys(value, path, ("type", "description"), _BENEFIT_KEYS):
            return None
        benefit_type = self.enum(BenefitType, value.get("type"), f"{path}.type")
        description = self.text(value, "description", path)
        amount = value.get("amount_inr")
        if amount is not None and (
            isinstance(amount, bool) or not isinstance(amount, int | float) or amount < 0
        ):
            self.error(f"{path}.amount_inr", f"must be a non-negative number or null, got {amount!r}")
            amount = None
        frequency = value.get("frequency")
        if frequency is not None:
            frequency = self.enum(BenefitFrequency, frequency, f"{path}.frequency")
        is_cash = value.get("is_cash", True)
        if not isinstance(is_cash, bool):
            self.error(f"{path}.is_cash", f"must be true or false, got {is_cash!r}")
            is_cash = True
        if benefit_type is None or description is None:
            return None
        return Benefit(benefit_type, description, amount, frequency, is_cash)

    def documents(self, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            self.error("$.required_documents", "must be a list of document type ids")
            return ()
        docs: list[str] = []
        for i, doc in enumerate(value):
            if doc not in DOCUMENT_TYPES:
                self.error(
                    f"$.required_documents[{i}]",
                    f"unknown document type {doc!r} (known: {', '.join(DOCUMENT_TYPES)})",
                )
            elif doc in docs:
                self.error(f"$.required_documents[{i}]", f"duplicate document type {doc!r}")
            else:
                docs.append(doc)
        return tuple(docs)

    def relationships(self, value: Any) -> tuple[Relationship, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            self.error("$.relationships", "must be a list")
            return ()
        result: list[Relationship] = []
        for i, item in enumerate(value):
            path = f"$.relationships[{i}]"
            if not self.keys(item, path, _RELATION_KEYS, _RELATION_KEYS):
                continue
            target = item.get("scheme_id")
            if not isinstance(target, str) or not _ID_RE.match(target):
                self.error(f"{path}.scheme_id", f"must be a snake_case scheme id, got {target!r}")
                continue
            relation = item.get("relation")
            if relation not in RELATION_TYPES:
                self.error(
                    f"{path}.relation",
                    f"invalid relation {relation!r} (allowed: {', '.join(RELATION_TYPES)})",
                )
                continue
            reason = self.text(item, "reason", path)
            url = self.url(item.get("source_url"), f"{path}.source_url")
            if reason and url:
                result.append(Relationship(target, relation, reason, url))
        return tuple(result)

    def conditions(self, value: Any, path: str) -> tuple[Condition | ConditionGroup, ...]:
        if not isinstance(value, list) or not value:
            self.error(path, "must be a non-empty list of conditions")
            return ()
        items = (self.condition(item, f"{path}[{i}]") for i, item in enumerate(value))
        return tuple(item for item in items if item is not None)

    def condition_id(self, item: Mapping[str, Any], path: str) -> str | None:
        cid = item.get("id")
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            self.error(f"{path}.id", f"must be snake_case ([a-z][a-z0-9_]*), got {cid!r}")
            return None
        if cid in self.condition_ids:
            self.error(f"{path}.id", f"duplicate condition id {cid!r} in this scheme")
            return None
        self.condition_ids.add(cid)
        return cid

    def condition(self, item: Any, path: str) -> Condition | ConditionGroup | None:
        if isinstance(item, Mapping) and ("all" in item or "any" in item):
            return self.group(item, path)
        if not self.keys(item, path, ("id", "description", "field", "operator", "source_ref"), _LEAF_KEYS):
            if not isinstance(item, Mapping):
                return None
        cid = self.condition_id(item, path)
        description = self.text(item, "description", path)
        failure_message = self.text(item, "failure_message", path)
        source_ref = self.text(item, "source_ref", path)
        field_name = item.get("field")
        spec = PROFILE_FIELDS.get(field_name) if isinstance(field_name, str) else None
        if spec is None:
            self.error(f"{path}.field", f"unknown profile field {field_name!r} (see fields.PROFILE_FIELDS)")
        op_name = item.get("operator")
        operator = OPERATORS.get(op_name) if isinstance(op_name, str) else None
        if operator is None:
            self.error(f"{path}.operator", f"unknown operator {op_name!r} (allowed: {', '.join(OPERATORS)})")
        min_trust = self.enum(
            Provenance, item.get("min_trust", Provenance.SELF_DECLARED.value), f"{path}.min_trust"
        )
        resolves_with = self.resolves_with(item.get("resolves_with"), f"{path}.resolves_with")
        value: Any = None
        if spec is not None and operator is not None:
            if spec.type not in operator.field_types:
                allowed = ", ".join(sorted(t.value for t in operator.field_types))
                self.error(
                    f"{path}.operator",
                    f"operator '{operator.name}' cannot be used on {spec.type.value} field '{spec.name}' "
                    f"(allowed field types: {allowed})",
                )
            else:
                value = self.operand(spec, operator.shape, item, path)
        if cid is None or description is None or spec is None or operator is None or min_trust is None:
            return None
        return Condition(
            id=cid,
            description=description,
            field=spec.name,
            operator=operator.name,
            value=value,
            min_trust=min_trust,
            resolves_with=resolves_with,
            failure_message=failure_message,
            source_ref=source_ref,
        )

    def group(self, item: Mapping[str, Any], path: str) -> ConditionGroup | None:
        self.keys(item, path, ("id", "description"), _GROUP_KEYS)
        if "all" in item and "any" in item:
            self.error(path, "a group must have exactly one of 'all' or 'any'")
            return None
        mode = GroupMode.ALL if "all" in item else GroupMode.ANY
        cid = self.condition_id(item, path)
        description = self.text(item, "description", path)
        failure_message = self.text(item, "failure_message", path)
        source_ref = self.text(item, "source_ref", path)
        children = self.conditions(item.get(mode.value), f"{path}.{mode.value}")
        if cid is None or description is None or not children:
            return None
        return ConditionGroup(cid, description, mode, children, failure_message, source_ref)

    def resolves_with(self, value: Any, path: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            self.error(path, "must be a list of document type ids or profile field names")
            return ()
        result: list[str] = []
        for i, ref in enumerate(value):
            if ref not in DOCUMENT_TYPES and ref not in PROFILE_FIELDS:
                self.error(f"{path}[{i}]", f"{ref!r} is neither a document type id nor a profile field")
            elif ref not in result:
                result.append(ref)
        return tuple(result)

    def operand(self, spec: FieldSpec, shape: ValueShape, item: Mapping[str, Any], path: str) -> Any:
        has_value = "value" in item and item["value"] is not None
        vpath = f"{path}.value"
        if shape is ValueShape.NONE:
            if has_value:
                self.error(vpath, f"operator '{item['operator']}' takes no value")
            return None
        if not has_value:
            self.error(vpath, f"operator '{item['operator']}' requires a value")
            return None
        raw = item["value"]
        if shape is ValueShape.SCALAR:
            return self.scalar(spec, raw, vpath)
        if not isinstance(raw, list):
            self.error(vpath, f"operator '{item['operator']}' requires a list, got {raw!r}")
            return None
        if shape is ValueShape.RANGE:
            if len(raw) != 2:
                self.error(vpath, f"'between' requires [low, high], got {raw!r}")
                return None
            low, high = (self.scalar(spec, v, f"{vpath}[{i}]") for i, v in enumerate(raw))
            if low is not None and high is not None and low > high:
                self.error(vpath, f"low bound {low} is greater than high bound {high}")
                return None
            return (low, high)
        if not raw:
            self.error(vpath, "list must not be empty")
            return None
        values = tuple(self.scalar(spec, v, f"{vpath}[{i}]") for i, v in enumerate(raw))
        if len(set(values)) != len(values):
            self.error(vpath, "list contains duplicate values")
        return values

    def scalar(self, spec: FieldSpec, raw: Any, path: str) -> Any:
        expected = {
            FieldType.INTEGER: (int,),
            FieldType.NUMBER: (int, float),
            FieldType.BOOLEAN: (bool,),
            FieldType.STRING: (str,),
            FieldType.ENUM: (str,),
        }[spec.type]
        if (isinstance(raw, bool) and spec.type is not FieldType.BOOLEAN) or not isinstance(raw, expected):
            self.error(path, f"{raw!r} is not a valid {spec.type.value} value for field '{spec.name}'")
            return None
        if spec.type is FieldType.ENUM and raw not in spec.choices:
            self.error(
                path, f"{raw!r} is not a valid choice for '{spec.name}' (choices: {', '.join(spec.choices)})"
            )
            return None
        try:
            return coerce_value(spec.name, raw)
        except ValueError as exc:
            self.error(path, str(exc))
            return None


def parse_rule(data: Any, origin: str = "<memory>") -> SchemeRule:
    """Validate one already-parsed YAML document and build a ``SchemeRule`` (with ``rule_hash``)."""
    parser = _Parser(origin)
    rule = parser.rule(data)
    if parser.errors or rule is None:
        raise RuleValidationError(parser.errors or [f"{origin}: $: invalid rule definition"])
    return rule


def load_rule_file(path: str | Path) -> SchemeRule:
    file = Path(path)
    try:
        data = yaml.load(file.read_text(encoding="utf-8"), Loader=_StrictLoader)  # noqa: S506 - safe subclass
    except yaml.YAMLError as exc:
        raise RuleValidationError([f"{file}: YAML syntax error: {exc}"]) from exc
    return parse_rule(data, origin=str(file))


def validate_rule_set(rules: Iterable[SchemeRule]) -> None:
    """Cross-file checks: duplicate versions, overlapping validity periods, relationship targets."""
    rules = list(rules)
    errors: list[str] = []
    by_scheme: dict[str, list[SchemeRule]] = {}
    for rule in rules:
        by_scheme.setdefault(rule.scheme_id, []).append(rule)
    for scheme_id, versions in sorted(by_scheme.items()):
        seen: set[str] = set()
        for rule in versions:
            if rule.version in seen:
                errors.append(f"{scheme_id}: duplicate version '{rule.version}'")
            seen.add(rule.version)
        ordered = sorted(versions, key=lambda r: r.effective_from)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if earlier.effective_to is None or earlier.effective_to >= later.effective_from:
                errors.append(
                    f"{scheme_id}: version '{earlier.version}' (effective {earlier.effective_from} to "
                    f"{earlier.effective_to or 'open-ended'}) overlaps version '{later.version}' "
                    f"(from {later.effective_from}); set effective_to on the older version"
                )
    for rule in rules:
        for i, rel in enumerate(rule.relationships):
            if rel.scheme_id == rule.scheme_id:
                errors.append(f"{rule.scheme_id}@{rule.version}: relationships[{i}] points at itself")
            elif rel.scheme_id not in by_scheme:
                errors.append(
                    f"{rule.scheme_id}@{rule.version}: relationships[{i}].scheme_id {rel.scheme_id!r} "
                    f"is not a loaded scheme (known: {', '.join(sorted(by_scheme))})"
                )
    if errors:
        raise RuleValidationError(errors)


def load_rules(path: str | Path = DEFAULT_RULES_DIR) -> list[SchemeRule]:
    """Load and validate a rule file or every ``*.yaml``/``*.yml`` file below a directory."""
    root = Path(path)
    if root.is_dir():
        files = sorted(p for p in root.rglob("*") if p.suffix in (".yaml", ".yml") and p.is_file())
    elif root.is_file():
        files = [root]
    else:
        raise RuleValidationError([f"{root}: rules path does not exist"])
    if not files:
        raise RuleValidationError([f"{root}: no rule files (*.yaml) found"])
    rules: list[SchemeRule] = []
    errors: list[str] = []
    for file in files:
        try:
            rules.append(load_rule_file(file))
        except RuleValidationError as exc:
            errors.extend(exc.errors)
    if errors:
        raise RuleValidationError(errors)
    validate_rule_set(rules)
    return sorted(rules, key=lambda r: (r.scheme_id, r.effective_from))

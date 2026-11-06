"""Normalize source records into the configured output schema."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .csvio import detect_dialect, header_is_likely
from .errors import ConfigError


_NO_DEFAULT = object()


@dataclass(frozen=True)
class MappingRule:
    """Map one source field to an output field, optionally with a fallback."""

    source_column: str
    output_column: str
    default_if_missing: Any = _NO_DEFAULT

    @property
    def has_default_if_missing(self) -> bool:
        return self.default_if_missing is not _NO_DEFAULT


def input_format(source: dict[str, Any]) -> str:
    """Return the explicit source format, or infer it from a common extension."""
    source_format = source.get("format")
    if source_format is None:
        suffix = str(source.get("path", "")).lower()
        if suffix.endswith(".jsonl"):
            source_format = "jsonl"
        elif suffix.endswith(".json"):
            source_format = "json"
        else:
            source_format = "csv"
    if source_format not in {"csv", "jsonl", "json", "stix"}:
        raise ConfigError("Each input format must be 'csv', 'jsonl', 'json', or 'stix'.")
    return source_format


def json_value_to_csv(value: Any) -> str:
    """Keep JSON scalar values legible and nested values valid in one CSV field."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def mapping_parts(source: dict[str, Any]) -> tuple[list[MappingRule], dict[str, Any]]:
    """Separate source mappings, optional defaults, and fixed output values."""
    mapping = source["mapping"]
    constants = mapping.get("$constants", {})
    rules: list[MappingRule] = []
    for source_column, specification in mapping.items():
        if source_column == "$constants":
            continue
        if isinstance(specification, str):
            rules.append(MappingRule(source_column, specification))
            continue
        if not isinstance(specification, dict):
            raise ConfigError(
                "Each mapping value must be an output column name or an object with "
                "output_column and default_if_missing."
            )
        if set(specification) != {"output_column", "default_if_missing"}:
            raise ConfigError(
                "A default mapping must contain exactly output_column and default_if_missing."
            )
        output_column = specification["output_column"]
        if not isinstance(output_column, str) or not output_column:
            raise ConfigError("mapping.output_column must be a non-empty output column name.")
        rules.append(
            MappingRule(source_column, output_column, specification["default_if_missing"])
        )
    return rules, constants


def row_with_constants(
    fields: list[str], constants: dict[str, Any], mapping_rules: list[MappingRule]
) -> dict[str, str]:
    row = {field: "" for field in fields}
    for target, value in constants.items():
        row[target] = json_value_to_csv(value)
    for rule in mapping_rules:
        if rule.has_default_if_missing:
            row[rule.output_column] = json_value_to_csv(rule.default_if_missing)
    return row


def resolve_header(source: dict[str, Any], path: Path, encoding: str, options: dict[str, Any]) -> bool:
    header = source.get("header", True)
    if header != "auto":
        return bool(header)
    _, sample = detect_dialect(path, encoding)
    return header_is_likely(sample, options["delimiter"])


def make_index_mapping(
    source: dict[str, Any], mapping_rules: list[MappingRule], header_row: list[str] | None,
    output_fields: list[str], delimiter: str,
) -> list[tuple[int, MappingRule]]:
    result: list[tuple[int, MappingRule]] = []
    header_index = {name: index for index, name in enumerate(header_row or [])}
    if header_row and len(header_index) != len(header_row):
        raise ConfigError(f"Input {source['path']} has duplicate header names.")
    for rule in mapping_rules:
        source_key = rule.source_column
        if source_key.isdigit():
            result.append((int(source_key) - 1, rule))
        elif header_row is None:
            if rule.has_default_if_missing:
                continue
            raise ConfigError(
                f"Input {source['path']} has no header, so '{source_key}' cannot be mapped by name."
            )
        elif source_key not in header_index:
            if rule.has_default_if_missing:
                continue
            available = ", ".join(repr(name) for name in header_row)
            message = (
                f"Input {source['path']} has no header named {source_key!r} when parsed with "
                f"delimiter {delimiter!r}. Available headers: {available or '(none)'}."
            )
            if len(header_row) == 1:
                possible = [candidate for candidate in (",", ";", "\t", "|", ":") if candidate != delimiter and candidate in header_row[0]]
                if possible:
                    message += (
                        f" The header was parsed as one field and contains {possible[0]!r}; "
                        "check the input delimiter configuration."
                    )
            raise ConfigError(message)
        else:
            result.append((header_index[source_key], rule))
    return result

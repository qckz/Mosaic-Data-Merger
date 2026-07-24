"""Normalize source records into the configured output schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .csvio import detect_dialect, header_is_likely
from .errors import ConfigError


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


def mapping_parts(source: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    """Separate ordinary source-field mappings from fixed output values."""
    mapping = source["mapping"]
    constants = mapping.get("$constants", {})
    source_mapping = {key: target for key, target in mapping.items() if key != "$constants"}
    return source_mapping, constants


def row_with_constants(fields: list[str], constants: dict[str, Any]) -> dict[str, str]:
    row = {field: "" for field in fields}
    for target, value in constants.items():
        row[target] = json_value_to_csv(value)
    return row


def resolve_header(source: dict[str, Any], path: Path, encoding: str, options: dict[str, Any]) -> bool:
    header = source.get("header", True)
    if header != "auto":
        return bool(header)
    _, sample = detect_dialect(path, encoding)
    return header_is_likely(sample, options["delimiter"])


def make_index_mapping(
    source: dict[str, Any], source_mapping: dict[str, str], header_row: list[str] | None,
    output_fields: list[str], delimiter: str,
) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    header_index = {name: index for index, name in enumerate(header_row or [])}
    if header_row and len(header_index) != len(header_row):
        raise ConfigError(f"Input {source['path']} has duplicate header names.")
    for source_key, target in source_mapping.items():
        if source_key.isdigit():
            result.append((int(source_key) - 1, target))
        elif header_row is None:
            raise ConfigError(
                f"Input {source['path']} has no header, so '{source_key}' cannot be mapped by name."
            )
        elif source_key not in header_index:
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
            result.append((header_index[source_key], target))
    return result

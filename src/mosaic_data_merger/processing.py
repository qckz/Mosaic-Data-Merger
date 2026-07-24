"""Row-level transformations, filters, and validation."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .errors import ConfigError, RowError


def apply_action(value: str, action: dict[str, Any]) -> str:
    action_type = action.get("type")
    if action_type == "trim":
        return value.strip()
    if action_type == "lowercase":
        return value.lower()
    if action_type == "uppercase":
        return value.upper()
    if action_type == "title_case":
        return value.title()
    if action_type == "set_default":
        return str(action.get("value", "")) if value == "" else value
    if action_type == "replace":
        if "old" not in action:
            raise ConfigError("replace transformation requires 'old'.")
        return value.replace(str(action["old"]), str(action.get("new", "")))
    if action_type == "null_if":
        values = action.get("values", [])
        if not isinstance(values, list):
            raise ConfigError("null_if transformation requires a values list.")
        return "" if value in {str(item) for item in values} else value
    if action_type == "date_format":
        if value == "":
            return value
        formats = action.get("input_formats")
        output_format = action.get("output_format")
        if not isinstance(formats, list) or not formats or not isinstance(output_format, str):
            raise ConfigError("date_format requires input_formats and output_format.")
        for input_format in formats:
            try:
                return dt.datetime.strptime(value, input_format).strftime(output_format)
            except ValueError:
                continue
        raise RowError(f"cannot parse date '{value}'")
    raise ConfigError(f"Unsupported transformation type: {action_type!r}")


def apply_transformations(row: dict[str, str], config: dict[str, Any]) -> None:
    for column, actions in config.get("transformations", {}).items():
        for action in actions:
            if not isinstance(action, dict):
                raise ConfigError("Each transformation action must be an object.")
            row[column] = apply_action(row[column], action)


def filter_matches(row: dict[str, str], condition: dict[str, Any]) -> bool:
    column = condition.get("column")
    if column not in row:
        raise ConfigError(f"Filter references unknown output column: {column!r}")
    value = row[column]
    operator = condition.get("operator")
    expected = condition.get("value")
    if operator == "not_empty":
        return value != ""
    if operator == "empty":
        return value == ""
    if operator == "equals":
        return value == str(expected)
    if operator == "not_equals":
        return value != str(expected)
    if operator == "contains":
        return str(expected) in value
    if operator == "in":
        return value in {str(item) for item in condition.get("values", [])}
    if operator == "regex":
        return re.search(str(expected), value) is not None
    raise ConfigError(f"Unsupported filter operator: {operator!r}")


def validate_row(row: dict[str, str], rules: list[dict[str, Any]]) -> None:
    for rule in rules:
        column = rule.get("column")
        if column not in row:
            raise ConfigError(f"Validation rule references unknown output column: {column!r}")
        value = row[column]
        rule_type = rule.get("type")
        if rule_type == "required" and value == "":
            raise RowError(f"{column} is required")
        if value == "":
            continue
        if rule_type == "date":
            date_format = rule.get("format")
            if not isinstance(date_format, str):
                raise ConfigError("date validation requires a format.")
            try:
                dt.datetime.strptime(value, date_format)
            except ValueError as error:
                raise RowError(f"{column} is not a valid date") from error
        elif rule_type == "number":
            try:
                float(value)
            except ValueError as error:
                raise RowError(f"{column} is not numeric") from error
        elif rule_type == "regex":
            if re.search(str(rule.get("pattern", "")), value) is None:
                raise RowError(f"{column} does not match required pattern")
        elif rule_type not in {"required", "date", "number", "regex"}:
            raise ConfigError(f"Unsupported validation rule type: {rule_type!r}")

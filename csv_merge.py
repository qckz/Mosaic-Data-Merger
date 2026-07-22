#!/usr/bin/env python3
"""Stream and normalize differently shaped CSV files into one CSV output.

The program intentionally uses only Python's standard library, so it is easy
to use in scheduled jobs and restricted automation environments.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when the job configuration cannot be safely executed."""


class RowError(ValueError):
    """Raised for a row that cannot be transformed or validated."""


def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(config, dict):
        raise ConfigError("The configuration root must be a JSON object.")
    # A scheduled job is often launched from a different current directory.
    # Keep relative job paths relative to the configuration file instead.
    config["_config_directory"] = str(path.resolve().parent)
    return config


def config_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(config.get("_config_directory", Path.cwd())) / path


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


def detect_dialect(path: Path, encoding: str) -> tuple[str, str]:
    """Return delimiter and a short sample, falling back predictably to comma."""
    try:
        with path.open("r", encoding=encoding, newline="") as handle:
            sample = handle.read(65_536)
    except UnicodeDecodeError as error:
        raise ConfigError(f"Cannot decode {path} as {encoding}: {error}") from error
    if not sample:
        return ",", sample
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|:")
        return dialect.delimiter, sample
    except csv.Error:
        return ",", sample


def header_is_likely(sample: str, delimiter: str) -> bool:
    if not sample:
        return False
    try:
        return csv.Sniffer().has_header(sample)
    except csv.Error:
        return False


def csv_options(spec: dict[str, Any], default_delimiter: str | None = None) -> dict[str, Any]:
    delimiter = spec.get("delimiter", default_delimiter)
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise ConfigError("Each delimiter must be exactly one character.")
    options: dict[str, Any] = {"delimiter": delimiter}
    for key in ("quotechar", "escapechar"):
        if key in spec:
            value = spec[key]
            if value is not None and (not isinstance(value, str) or len(value) != 1):
                raise ConfigError(f"{key} must be one character or null.")
            options[key] = value
    for key in ("doublequote", "skipinitialspace", "strict"):
        if key in spec:
            options[key] = bool(spec[key])
    return options


def output_columns(config: dict[str, Any]) -> list[str]:
    output = config.get("output")
    if not isinstance(output, dict):
        raise ConfigError("'output' must be an object.")
    columns = output.get("columns")
    if not isinstance(columns, list) or not columns or not all(
        isinstance(column, str) and column for column in columns
    ):
        raise ConfigError("output.columns must be a non-empty list of column names.")
    if len(set(columns)) != len(columns):
        raise ConfigError("output.columns contains duplicate names.")
    if output.get("add_provenance", False):
        for column in ("source_file", "source_row"):
            if column not in columns:
                columns = [*columns, column]
    return columns


def validate_config(config: dict[str, Any], check_inputs: bool = True) -> list[str]:
    columns = output_columns(config)
    output = config["output"]
    if not isinstance(output.get("path"), str) or not output["path"]:
        raise ConfigError("output.path must be a non-empty path.")
    output_format = output.get("format", "csv")
    if output_format not in {"csv", "json", "stix"}:
        raise ConfigError("output.format must be 'csv', 'json', or 'stix'.")
    csv_options(output, ",")
    if output.get("mode", "replace") not in {"replace", "append"}:
        raise ConfigError("output.mode must be 'replace' or 'append'.")
    if output_format != "csv" and output.get("mode", "replace") == "append":
        raise ConfigError("output.mode 'append' is available only for CSV output.")
    if output.get("mode", "replace") == "append" and output.get("atomic_write", False):
        raise ConfigError("atomic_write is only available with output.mode 'replace'.")
    if output_format == "stix":
        stix = output.get("stix")
        if not isinstance(stix, dict):
            raise ConfigError("output.stix must define STIX Bundle settings for STIX output.")
        object_type = stix.get("object_type", "note")
        if not isinstance(object_type, str) or not object_type:
            raise ConfigError("output.stix.object_type must be a non-empty STIX object type.")
        properties = stix.get("properties", {})
        if not isinstance(properties, dict):
            raise ConfigError("output.stix.properties must map output columns to STIX property names.")
        if any(column not in columns or not isinstance(property_name, str) or not property_name for column, property_name in properties.items()):
            raise ConfigError("STIX property mappings must map output columns to non-empty property names.")
        static = stix.get("static", {})
        if not isinstance(static, dict):
            raise ConfigError("output.stix.static must be an object.")
        try:
            json.dumps(static)
        except (TypeError, ValueError) as error:
            raise ConfigError("output.stix.static must contain JSON-compatible values.") from error

    inputs = config.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ConfigError("inputs must be a non-empty list.")
    for index, source in enumerate(inputs, start=1):
        if not isinstance(source, dict):
            raise ConfigError(f"inputs[{index}] must be an object.")
        path = source.get("path")
        if not isinstance(path, str) or not path:
            raise ConfigError(f"inputs[{index}].path must be a non-empty path.")
        if check_inputs and not config_path(config, path).is_file():
            raise ConfigError(f"Input file does not exist: {path}")
        source_kind = input_format(source)
        if source_kind == "csv":
            header = source.get("header", True)
            if header not in {True, False, "auto"}:
                raise ConfigError(f"inputs[{index}].header must be true, false, or 'auto'.")
            if "delimiter" in source:
                csv_options(source)
        elif "header" in source or "delimiter" in source:
            raise ConfigError(f"inputs[{index}] is {source_kind.upper()}; header and delimiter do not apply.")
        mapping = source.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise ConfigError(f"inputs[{index}].mapping must be a non-empty object.")
        targets = list(mapping.values())
        if not all(isinstance(target, str) and target in columns for target in targets):
            raise ConfigError(
                f"inputs[{index}].mapping targets must be names in output.columns."
            )
        if len(set(targets)) != len(targets):
            raise ConfigError(f"inputs[{index}] maps more than one source field to an output field.")
        for source_column in mapping:
            if not isinstance(source_column, str) or not source_column:
                raise ConfigError(f"inputs[{index}] has an invalid mapping key.")
            if source_kind == "csv" and source_column.isdigit() and int(source_column) < 1:
                raise ConfigError("Numeric source column mappings are 1-based (minimum 1).")
        if source.get("on_malformed_row", "error") not in {"error", "skip", "pad"}:
            raise ConfigError("on_malformed_row must be 'error', 'skip', or 'pad'.")
        if source_kind in {"jsonl", "json", "stix"} and source.get("on_malformed_row") == "pad":
            raise ConfigError(f"{source_kind.upper()} inputs support on_malformed_row 'error' or 'skip', not 'pad'.")

    transforms = config.get("transformations", {})
    if not isinstance(transforms, dict):
        raise ConfigError("transformations must be an object keyed by output column.")
    for column, actions in transforms.items():
        if column not in columns or not isinstance(actions, list):
            raise ConfigError("Each transformation column must exist and contain an action list.")

    validation = config.get("validation", {})
    if not isinstance(validation, dict):
        raise ConfigError("validation must be an object.")
    if validation.get("on_error", "error") not in {"error", "reject", "skip"}:
        raise ConfigError("validation.on_error must be 'error', 'reject', or 'skip'.")
    if validation.get("on_error") == "reject" and not validation.get("rejects_path"):
        raise ConfigError("validation.rejects_path is required when validation.on_error is 'reject'.")
    rules = validation.get("rules", [])
    if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
        raise ConfigError("validation.rules must be a list of rule objects.")
    for rule in rules:
        if rule.get("column") not in columns:
            raise ConfigError("Each validation rule must reference an output column.")

    filters = config.get("filters", [])
    if not isinstance(filters, list) or not all(isinstance(condition, dict) for condition in filters):
        raise ConfigError("filters must be a list of condition objects.")
    for condition in filters:
        if condition.get("column") not in columns:
            raise ConfigError("Each filter must reference an output column.")

    dedupe = config.get("deduplication", {})
    if dedupe and not isinstance(dedupe, dict):
        raise ConfigError("deduplication must be an object.")
    if dedupe.get("enabled", False):
        keys = dedupe.get("keys")
        if not isinstance(keys, list) or not keys or any(key not in columns for key in keys):
            raise ConfigError("deduplication.keys must be non-empty output column names.")
        if dedupe.get("keep", "first") != "first":
            raise ConfigError("Streaming deduplication currently supports only keep: 'first'.")
    return columns


def resolve_header(source: dict[str, Any], path: Path, encoding: str, options: dict[str, Any]) -> bool:
    header = source.get("header", True)
    if header != "auto":
        return bool(header)
    _, sample = detect_dialect(path, encoding)
    return header_is_likely(sample, options["delimiter"])


def make_index_mapping(
    source: dict[str, Any], header_row: list[str] | None, output_fields: list[str]
) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    header_index = {name: index for index, name in enumerate(header_row or [])}
    if header_row and len(header_index) != len(header_row):
        raise ConfigError(f"Input {source['path']} has duplicate header names.")
    for source_key, target in source["mapping"].items():
        if source_key.isdigit():
            result.append((int(source_key) - 1, target))
        elif header_row is None:
            raise ConfigError(
                f"Input {source['path']} has no header, so '{source_key}' cannot be mapped by name."
            )
        elif source_key not in header_index:
            raise ConfigError(f"Input {source['path']} has no header named '{source_key}'.")
        else:
            result.append((header_index[source_key], target))
    return result


def handle_normalized_row(
    row: dict[str, str],
    path: Path,
    source_row_number: int,
    config: dict[str, Any],
    validation: dict[str, Any],
    validation_rules: list[dict[str, Any]],
    dedupe: "DiskDeduplicator | None",
    writer: csv.DictWriter | None,
    reject_writer: csv.DictWriter | None,
    stats: dict[str, Any],
    source_stats: Counter[str],
) -> None:
    try:
        apply_transformations(row, config)
        if not all(filter_matches(row, condition) for condition in config.get("filters", [])):
            stats["rows_filtered"] += 1
            source_stats["rows_filtered"] += 1
            return
        validate_row(row, validation_rules)
    except RowError as error:
        policy = validation.get("on_error", "error")
        if policy == "error":
            raise RowError(f"{path}:{source_row_number}: {error}") from error
        if policy == "reject":
            stats["rows_rejected"] += 1
            source_stats["rows_rejected"] += 1
            if reject_writer:
                reject_writer.writerow({**row, "_error": str(error)})
        else:
            stats["rows_skipped"] += 1
            source_stats["rows_skipped"] += 1
        return
    if dedupe and dedupe.seen_before(row):
        stats["rows_deduplicated"] += 1
        source_stats["rows_deduplicated"] += 1
        return
    stats["rows_written"] += 1
    source_stats["rows_written"] += 1
    if writer:
        writer.writerow(row)


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


class DiskDeduplicator:
    """A bounded-memory key set backed by a temporary SQLite database."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        fd, name = tempfile.mkstemp(prefix="csv-merge-dedupe-", suffix=".sqlite3")
        os.close(fd)
        self.path = name
        self.connection = sqlite3.connect(name)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("CREATE TABLE seen (key TEXT PRIMARY KEY)")

    def seen_before(self, row: dict[str, str]) -> bool:
        key = json.dumps([row[column] for column in self.keys], ensure_ascii=False, separators=(",", ":"))
        try:
            self.connection.execute("INSERT INTO seen(key) VALUES (?)", (key,))
            return False
        except sqlite3.IntegrityError:
            return True

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()
        Path(self.path).unlink(missing_ok=True)


class JsonArrayWriter:
    """Write a JSON array incrementally, avoiding an in-memory output list."""

    def __init__(self, handle: Any) -> None:
        self.handle = handle
        self.first = True
        self.handle.write("[")

    def writerow(self, row: dict[str, str]) -> None:
        if not self.first:
            self.handle.write(",")
        json.dump(row, self.handle, ensure_ascii=False, separators=(",", ":"))
        self.first = False

    def close(self) -> None:
        self.handle.write("]\n")
        self.handle.close()


class StixBundleWriter:
    """Write one configured STIX 2.1 object per normalized row into a Bundle."""

    def __init__(self, handle: Any, specification: dict[str, Any]) -> None:
        self.handle = handle
        self.specification = specification
        self.first = True
        self.object_type = specification.get("object_type", "note")
        self.property_map = specification.get("properties", {})
        self.static = specification.get("static", {})
        self.timestamp = specification.get("timestamp") or dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        bundle_id = specification.get("bundle_id") or f"bundle--{uuid.uuid4()}"
        self.handle.write('{"type":"bundle","id":')
        json.dump(bundle_id, self.handle, ensure_ascii=False)
        self.handle.write(',"objects":[')

    def writerow(self, row: dict[str, str]) -> None:
        record = dict(self.static)
        record["type"] = self.object_type
        record.setdefault("spec_version", "2.1")
        record.setdefault("id", f"{self.object_type}--{uuid.uuid4()}")
        if self.specification.get("add_timestamps", True):
            record.setdefault("created", self.timestamp)
            record.setdefault("modified", self.timestamp)
        for source_column, property_name in self.property_map.items():
            if row[source_column] != "":
                record[property_name] = row[source_column]
        if not self.first:
            self.handle.write(",")
        json.dump(record, self.handle, ensure_ascii=False, separators=(",", ":"))
        self.first = False

    def close(self) -> None:
        self.handle.write("]}\n")
        self.handle.close()


def atomic_path(destination: Path) -> tuple[Path, Path | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    return Path(temporary_name), Path(temporary_name)


def process(config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    fields = validate_config(config)
    output = config["output"]
    destination = config_path(config, output["path"])
    output_encoding = output.get("encoding", "utf-8")
    output_options = csv_options(output, ",")
    output_format = output.get("format", "csv")
    mode = output.get("mode", "replace")
    atomic = bool(output.get("atomic_write", mode == "replace")) and mode == "replace" and not dry_run
    target = destination
    temporary: Path | None = None
    if atomic:
        target, temporary = atomic_path(destination)
    elif not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)

    stats: dict[str, Any] = {
        "output": str(destination), "rows_read": 0, "rows_written": 0, "rows_filtered": 0,
        "rows_rejected": 0, "rows_skipped": 0, "rows_deduplicated": 0, "files": [],
        "dry_run": dry_run,
    }
    validation = config.get("validation", {})
    validation_rules = validation.get("rules", [])
    reject_handle = None
    reject_writer = None
    dedupe: DiskDeduplicator | None = None
    output_handle = None
    try:
        if validation.get("on_error") == "reject" and not dry_run:
            rejects_path = config_path(config, validation["rejects_path"])
            rejects_path.parent.mkdir(parents=True, exist_ok=True)
            reject_handle = rejects_path.open("w", encoding=output_encoding, newline="")
            reject_writer = csv.DictWriter(reject_handle, fieldnames=[*fields, "_error"], **output_options)
            reject_writer.writeheader()
        if config.get("deduplication", {}).get("enabled", False):
            dedupe = DiskDeduplicator(config["deduplication"]["keys"])

        write_header = output_format == "csv" and (mode == "replace" or not destination.exists() or destination.stat().st_size == 0)
        if output_format == "csv" and mode == "append" and destination.exists() and destination.stat().st_size > 0:
            with destination.open("r", encoding=output_encoding, newline="") as existing:
                current_header = next(csv.reader(existing, **output_options), None)
            if current_header != fields:
                raise ConfigError("Cannot append: the existing output header differs from output.columns.")

        writer = None
        if not dry_run:
            output_handle = target.open("w" if mode == "replace" else "a", encoding=output_encoding, newline="")
            if output_format == "csv":
                writer = csv.DictWriter(output_handle, fieldnames=fields, extrasaction="ignore", **output_options)
                if write_header:
                    writer.writeheader()
            elif output_format == "json":
                writer = JsonArrayWriter(output_handle)
            else:
                writer = StixBundleWriter(output_handle, output["stix"])

        for source in config["inputs"]:
            source_stats: Counter[str] = Counter()
            path = config_path(config, source["path"])
            encoding = source.get("encoding", "utf-8")
            source_kind = input_format(source)
            if source_kind == "csv":
                delimiter = source.get("delimiter")
                if delimiter is None:
                    delimiter, _ = detect_dialect(path, encoding)
                options = csv_options(source, delimiter)
                has_header = resolve_header(source, path, encoding, options)
                with path.open("r", encoding=encoding, newline="") as handle:
                    reader = csv.reader(handle, **options)
                    header = next(reader, None) if has_header else None
                    if has_header and header is None:
                        raise ConfigError(f"Input {path} declares a header but is empty.")
                    index_mapping = make_index_mapping(source, header, fields)
                    max_index = max((index for index, _ in index_mapping), default=-1)
                    for source_row_number, values in enumerate(reader, start=2 if has_header else 1):
                        stats["rows_read"] += 1
                        source_stats["rows_read"] += 1
                        row = {field: "" for field in fields}
                        if len(values) <= max_index:
                            malformed = source.get("on_malformed_row", "error")
                            message = f"row has {len(values)} column(s); mapping requires column {max_index + 1}"
                            if malformed == "error":
                                raise RowError(f"{path}:{source_row_number}: {message}")
                            if malformed == "skip":
                                stats["rows_skipped"] += 1
                                source_stats["rows_skipped"] += 1
                                continue
                        for index, target_field in index_mapping:
                            row[target_field] = values[index] if index < len(values) else ""
                        if output.get("add_provenance", False):
                            row["source_file"] = str(path)
                            row["source_row"] = str(source_row_number)
                        handle_normalized_row(
                            row, path, source_row_number, config, validation, validation_rules,
                            dedupe, writer, reject_writer, stats, source_stats,
                        )
                stats["files"].append({
                    "path": str(path), **dict(source_stats), "format": "csv",
                    "header_used": has_header, "delimiter": options["delimiter"],
                })
            elif source_kind == "jsonl":
                key_mapping = list(source["mapping"].items())
                with path.open("r", encoding=encoding, newline="") as handle:
                    for source_row_number, raw_line in enumerate(handle, start=1):
                        stats["rows_read"] += 1
                        source_stats["rows_read"] += 1
                        try:
                            record = json.loads(raw_line)
                            if not isinstance(record, dict):
                                raise ValueError("JSONL records must be JSON objects")
                        except (json.JSONDecodeError, ValueError) as error:
                            message = f"{path}:{source_row_number}: invalid JSONL record ({error})"
                            if source.get("on_malformed_row", "error") == "error":
                                raise RowError(message) from error
                            stats["rows_skipped"] += 1
                            source_stats["rows_skipped"] += 1
                            continue
                        row = {field: "" for field in fields}
                        for source_key, target_field in key_mapping:
                            row[target_field] = json_value_to_csv(record.get(source_key))
                        if output.get("add_provenance", False):
                            row["source_file"] = str(path)
                            row["source_row"] = str(source_row_number)
                        handle_normalized_row(
                            row, path, source_row_number, config, validation, validation_rules,
                            dedupe, writer, reject_writer, stats, source_stats,
                        )
                stats["files"].append({"path": str(path), **dict(source_stats), "format": "jsonl"})
            else:
                try:
                    with path.open("r", encoding=encoding) as handle:
                        document = json.load(handle)
                except json.JSONDecodeError as error:
                    raise RowError(f"{path}: invalid JSON document ({error})") from error
                if source_kind == "stix":
                    if not isinstance(document, dict):
                        raise RowError(f"{path}: a STIX document must be an object or Bundle.")
                    if document.get("type") == "bundle":
                        if not isinstance(document.get("id"), str) or not document["id"].startswith("bundle--"):
                            raise RowError(f"{path}: a STIX Bundle requires an id beginning with 'bundle--'.")
                        records = document.get("objects", [])
                        if not isinstance(records, list):
                            raise RowError(f"{path}: STIX Bundle objects must be a list.")
                    else:
                        records = [document]
                elif isinstance(document, list):
                    records = document
                else:
                    records = [document]
                for source_row_number, record in enumerate(records, start=1):
                    stats["rows_read"] += 1
                    source_stats["rows_read"] += 1
                    if not isinstance(record, dict):
                        message = f"{path}:{source_row_number}: JSON records must be objects"
                        if source.get("on_malformed_row", "error") == "error":
                            raise RowError(message)
                        stats["rows_skipped"] += 1
                        source_stats["rows_skipped"] += 1
                        continue
                    if source_kind == "stix" and (
                        not isinstance(record.get("type"), str)
                        or not isinstance(record.get("id"), str)
                        or not record["id"].startswith(f"{record['type']}--")
                    ):
                        message = f"{path}:{source_row_number}: STIX objects require type-prefixed string ids"
                        if source.get("on_malformed_row", "error") == "error":
                            raise RowError(message)
                        stats["rows_skipped"] += 1
                        source_stats["rows_skipped"] += 1
                        continue
                    row = {field: "" for field in fields}
                    for source_key, target_field in source["mapping"].items():
                        row[target_field] = json_value_to_csv(record.get(source_key))
                    if output.get("add_provenance", False):
                        row["source_file"] = str(path)
                        row["source_row"] = str(source_row_number)
                    handle_normalized_row(
                        row, path, source_row_number, config, validation, validation_rules,
                        dedupe, writer, reject_writer, stats, source_stats,
                    )
                stats["files"].append({"path": str(path), **dict(source_stats), "format": source_kind})
        if output_handle:
            if isinstance(writer, (JsonArrayWriter, StixBundleWriter)):
                writer.close()
            else:
                output_handle.close()
            output_handle = None
        if atomic and temporary:
            os.replace(temporary, destination)
            temporary = None
    finally:
        if output_handle:
            output_handle.close()
        if dedupe:
            dedupe.close()
        if reject_handle:
            reject_handle.close()
        if temporary:
            temporary.unlink(missing_ok=True)
    return stats


def inspect_file(path: Path, encoding: str, delimiter: str | None, source_format: str = "auto") -> dict[str, Any]:
    if source_format == "auto":
        source_format = "jsonl" if path.suffix.lower() == ".jsonl" else "json" if path.suffix.lower() == ".json" else "csv"
    if source_format not in {"csv", "jsonl", "json", "stix"}:
        raise ConfigError("inspect format must be 'csv', 'jsonl', 'json', 'stix', or 'auto'.")
    if source_format == "jsonl":
        rows: list[Any] = []
        with path.open("r", encoding=encoding) as handle:
            for source_row_number, raw_line in enumerate(handle, start=1):
                if len(rows) == 5:
                    break
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError as error:
                    raise ConfigError(f"{path}:{source_row_number}: invalid JSONL record ({error})") from error
                if not isinstance(record, dict):
                    raise ConfigError(f"{path}:{source_row_number}: JSONL records must be JSON objects.")
                rows.append(record)
        keys = sorted({key for row in rows for key in row})
        return {"path": str(path), "encoding": encoding, "format": "jsonl", "sample_keys": keys, "sample_rows": rows}
    if source_format in {"json", "stix"}:
        try:
            with path.open("r", encoding=encoding) as handle:
                document = json.load(handle)
        except json.JSONDecodeError as error:
            raise ConfigError(f"{path}: invalid JSON document ({error})") from error
        if source_format == "stix":
            if not isinstance(document, dict):
                raise ConfigError(f"{path}: a STIX document must be an object or Bundle.")
            rows = document.get("objects", []) if document.get("type") == "bundle" else [document]
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ConfigError(f"{path}: STIX Bundle objects must be an object list.")
            return {
                "path": str(path), "encoding": encoding, "format": "stix",
                "bundle_id": document.get("id") if document.get("type") == "bundle" else None,
                "object_count": len(rows), "sample_keys": sorted({key for row in rows[:5] for key in row}),
                "sample_rows": rows[:5],
            }
        rows = document if isinstance(document, list) else [document]
        if not all(isinstance(row, dict) for row in rows):
            raise ConfigError(f"{path}: JSON input must be an object or a list of objects.")
        return {
            "path": str(path), "encoding": encoding, "format": "json",
            "record_count": len(rows), "sample_keys": sorted({key for row in rows[:5] for key in row}),
            "sample_rows": rows[:5],
        }
    detected_delimiter, sample = detect_dialect(path, encoding)
    used_delimiter = delimiter or detected_delimiter
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=used_delimiter)
        rows = []
        for _, row in zip(range(5), reader):
            rows.append(row)
    return {
        "path": str(path), "encoding": encoding, "format": "csv", "detected_delimiter": detected_delimiter,
        "delimiter_used": used_delimiter, "header_likely": header_is_likely(sample, used_delimiter),
        "sample_rows": rows,
    }


def write_json(value: dict[str, Any], path: str | None = None) -> None:
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description="Transform CSV, JSON, JSONL, and STIX inputs into configured output.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="Show a CSV, JSON, JSONL, or STIX sample and layout.")
    inspect_parser.add_argument("path", type=Path)
    inspect_parser.add_argument("--encoding", default="utf-8")
    inspect_parser.add_argument("--delimiter")
    inspect_parser.add_argument("--format", choices=("auto", "csv", "json", "jsonl", "stix"), default="auto")
    validate_parser = subparsers.add_parser("validate", help="Validate a job configuration without writing output.")
    validate_parser.add_argument("--config", required=True, type=Path)
    run_parser = subparsers.add_parser("run", help="Run a CSV, JSON, JSONL, or STIX transformation job.")
    run_parser.add_argument("--config", required=True, type=Path)
    run_parser.add_argument("--dry-run", action="store_true", help="Process and report without producing files.")
    run_parser.add_argument("--report", help="Write the run summary to this JSON file as well as stdout.")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        if args.command == "inspect":
            if not args.path.is_file():
                raise ConfigError(f"Input file does not exist: {args.path}")
            write_json(inspect_file(args.path, args.encoding, args.delimiter, args.format))
        elif args.command == "validate":
            config = load_config(args.config)
            fields = validate_config(config)
            write_json({"valid": True, "output_columns": fields, "input_count": len(config["inputs"])})
        else:
            config = load_config(args.config)
            report = process(config, dry_run=args.dry_run)
            report["duration_seconds"] = round(time.monotonic() - started, 3)
            write_json(report)
            if args.report:
                write_json(report, args.report)
        return 0
    except (ConfigError, RowError, OSError, csv.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

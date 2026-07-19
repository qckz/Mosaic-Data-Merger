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
from collections import Counter
from pathlib import Path
from typing import Any, Iterator


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
    csv_options(output, ",")
    if output.get("mode", "replace") not in {"replace", "append"}:
        raise ConfigError("output.mode must be 'replace' or 'append'.")
    if output.get("mode", "replace") == "append" and output.get("atomic_write", False):
        raise ConfigError("atomic_write is only available with output.mode 'replace'.")

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
        header = source.get("header", True)
        if header not in {True, False, "auto"}:
            raise ConfigError(f"inputs[{index}].header must be true, false, or 'auto'.")
        if "delimiter" in source:
            csv_options(source)
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
            if source_column.isdigit() and int(source_column) < 1:
                raise ConfigError("Numeric source column mappings are 1-based (minimum 1).")
        if source.get("on_malformed_row", "error") not in {"error", "skip", "pad"}:
            raise ConfigError("on_malformed_row must be 'error', 'skip', or 'pad'.")

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

        write_header = mode == "replace" or not destination.exists() or destination.stat().st_size == 0
        if mode == "append" and destination.exists() and destination.stat().st_size > 0:
            with destination.open("r", encoding=output_encoding, newline="") as existing:
                current_header = next(csv.reader(existing, **output_options), None)
            if current_header != fields:
                raise ConfigError("Cannot append: the existing output header differs from output.columns.")

        writer = None
        if not dry_run:
            output_handle = target.open("w" if mode == "replace" else "a", encoding=output_encoding, newline="")
            writer = csv.DictWriter(output_handle, fieldnames=fields, extrasaction="ignore", **output_options)
            if write_header:
                writer.writeheader()

        for source in config["inputs"]:
            source_stats: Counter[str] = Counter()
            path = config_path(config, source["path"])
            encoding = source.get("encoding", "utf-8")
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
                    try:
                        apply_transformations(row, config)
                        if not all(filter_matches(row, condition) for condition in config.get("filters", [])):
                            stats["rows_filtered"] += 1
                            source_stats["rows_filtered"] += 1
                            continue
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
                        continue
                    if dedupe and dedupe.seen_before(row):
                        stats["rows_deduplicated"] += 1
                        source_stats["rows_deduplicated"] += 1
                        continue
                    stats["rows_written"] += 1
                    source_stats["rows_written"] += 1
                    if writer:
                        writer.writerow(row)
            stats["files"].append({"path": str(path), **dict(source_stats), "header_used": has_header, "delimiter": options["delimiter"]})
        if output_handle:
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


def inspect_file(path: Path, encoding: str, delimiter: str | None) -> dict[str, Any]:
    detected_delimiter, sample = detect_dialect(path, encoding)
    used_delimiter = delimiter or detected_delimiter
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=used_delimiter)
        rows = []
        for _, row in zip(range(5), reader):
            rows.append(row)
    return {
        "path": str(path), "encoding": encoding, "detected_delimiter": detected_delimiter,
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
    parser = argparse.ArgumentParser(description="Merge heterogeneous CSV inputs using a streaming JSON-defined job.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="Show a CSV sample and likely layout.")
    inspect_parser.add_argument("path", type=Path)
    inspect_parser.add_argument("--encoding", default="utf-8")
    inspect_parser.add_argument("--delimiter")
    validate_parser = subparsers.add_parser("validate", help="Validate a job configuration without writing output.")
    validate_parser.add_argument("--config", required=True, type=Path)
    run_parser = subparsers.add_parser("run", help="Run a CSV merge job.")
    run_parser.add_argument("--config", required=True, type=Path)
    run_parser.add_argument("--dry-run", action="store_true", help="Process and report without producing files.")
    run_parser.add_argument("--report", help="Write the run summary to this JSON file as well as stdout.")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        if args.command == "inspect":
            if not args.path.is_file():
                raise ConfigError(f"Input file does not exist: {args.path}")
            write_json(inspect_file(args.path, args.encoding, args.delimiter))
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

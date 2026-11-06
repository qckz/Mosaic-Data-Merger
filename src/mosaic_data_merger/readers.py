"""Shared CSV, JSON, JSONL, and STIX readers yielding normalized records."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .csvio import csv_options, detect_dialect
from .errors import ConfigError, RowError
from .mapping import (
    input_format, json_value_to_csv, make_index_mapping, mapping_parts,
    resolve_header, row_with_constants,
)


def read_rows(
    source: dict[str, Any], path: Path, fields: list[str],
    stats: dict[str, Any], info: Callable[[str], None] | None = None,
) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (record number, row); count malformed records, without retaining rows."""
    kind = input_format(source)
    stats.update(path=str(path), format=kind)
    encoding = source.get("encoding", "utf-8")
    rules, constants = mapping_parts(source)

    def count(name: str) -> None:
        stats[name] = stats.get(name, 0) + 1

    def invalid(number: int, message: str) -> None:
        if source.get("on_malformed_row", "error") == "error":
            raise RowError(f"{path}:{number}: {message}")
        count("rows_skipped")

    def mapped(record: dict[str, Any]) -> dict[str, str]:
        row = row_with_constants(fields, constants, rules)
        for rule in rules:
            if rule.source_column in record:
                row[rule.output_column] = json_value_to_csv(record[rule.source_column])
        return row

    if kind == "csv":
        delimiter = source.get("delimiter")
        if delimiter is None:
            delimiter, _ = detect_dialect(path, encoding)
        options = csv_options(source, delimiter)
        has_header = resolve_header(source, path, encoding, options)
        stats.update(header_used=has_header, delimiter=options["delimiter"])
        if info:
            info(f"Processing {path}: CSV delimiter={options['delimiter']!r}, header={'yes' if has_header else 'no'}.")
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.reader(handle, **options)
            header = next(reader, None) if has_header else None
            if has_header and header is None:
                raise ConfigError(f"Input {path} declares a header but is empty.")
            indices = make_index_mapping(source, rules, header, fields, options["delimiter"])
            required_index = max((index for index, rule in indices if not rule.has_default_if_missing), default=-1)
            for number, values in enumerate(reader, start=2 if has_header else 1):
                count("rows_read")
                if len(values) <= required_index:
                    message = (
                        f"row parsed as {len(values)} column(s) with delimiter {options['delimiter']!r}; "
                        f"mapping requires column {required_index + 1}. Check the delimiter or source mapping."
                    )
                    if source.get("on_malformed_row", "error") != "pad":
                        invalid(number, message)
                        continue
                row = row_with_constants(fields, constants, rules)
                for index, rule in indices:
                    if index < len(values):
                        row[rule.output_column] = values[index]
                yield number, row
        return

    if info:
        info(f"Processing {path}: {kind.upper()}.")
    if kind == "jsonl":
        with path.open("r", encoding=encoding, newline="") as handle:
            for number, line in enumerate(handle, start=1):
                count("rows_read")
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("JSONL records must be JSON objects")
                except ValueError as error:
                    invalid(number, f"invalid JSONL record ({error})")
                    continue
                yield number, mapped(record)
        return

    try:
        with path.open("r", encoding=encoding) as handle:
            document = json.load(handle)
    except json.JSONDecodeError as error:
        raise RowError(f"{path}: invalid JSON document ({error})") from error
    if kind == "stix":
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
    else:
        records = document if isinstance(document, list) else [document]
    for number, record in enumerate(records, start=1):
        count("rows_read")
        if not isinstance(record, dict):
            invalid(number, "JSON records must be objects")
            continue
        if kind == "stix" and (
            not isinstance(record.get("type"), str)
            or not isinstance(record.get("id"), str)
            or not record["id"].startswith(f"{record['type']}--")
        ):
            invalid(number, "STIX objects require type-prefixed string ids")
            continue
        yield number, mapped(record)

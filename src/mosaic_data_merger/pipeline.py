"""Streaming job orchestration and source inspection.

Format-specific parsing is coordinated here, while mapping, configuration,
row processing, output writers, and temporary stores live in focused modules.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import validate_config
from .csvio import detect_dialect, header_is_likely, output_csv_options
from .errors import ConfigError, RowError
from .mapping import input_format
from .paths import config_path, expand_input_paths
from .readers import read_rows
from .processing import apply_transformations, filter_matches, validate_row
from .stores import DiskDeduplicator, DiskExcluder, load_exclusions
from .writers import JsonArrayWriter, JsonLinesWriter, StixBundleWriter, atomic_path


def handle_normalized_row(
    row: dict[str, str],
    path: Path,
    source_row_number: int,
    config: dict[str, Any],
    validation: dict[str, Any],
    validation_rules: list[dict[str, Any]],
    excluder: "DiskExcluder | None",
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
        if excluder:
            matching_list = excluder.matching_list(row)
            if matching_list is not None:
                stats["rows_excluded"] += 1
                source_stats["rows_excluded"] += 1
                stats["exclusions"][matching_list]["rows_excluded"] += 1
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


def process(
    config: dict[str, Any], dry_run: bool = False, info: Callable[[str], None] | None = None
) -> dict[str, Any]:
    fields = validate_config(config)
    output = config["output"]
    destination = config_path(config, output["path"])
    output_encoding = output.get("encoding", "utf-8")
    output_options = output_csv_options(output)
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
        "rows_rejected": 0, "rows_skipped": 0, "rows_excluded": 0, "rows_deduplicated": 0,
        "files": [], "input_patterns": [], "exclusions": [], "dry_run": dry_run,
    }
    validation = config.get("validation", {})
    validation_rules = validation.get("rules", [])
    reject_handle = None
    reject_writer = None
    excluder: DiskExcluder | None = None
    dedupe: DiskDeduplicator | None = None
    output_handle = None
    try:
        if validation.get("on_error") == "reject" and not dry_run:
            rejects_path = config_path(config, validation["rejects_path"])
            rejects_path.parent.mkdir(parents=True, exist_ok=True)
            reject_handle = rejects_path.open("w", encoding=output_encoding, newline="")
            reject_writer = csv.DictWriter(reject_handle, fieldnames=[*fields, "_error"], **output_options)
            reject_writer.writeheader()
        excluder, stats["exclusions"] = load_exclusions(config, fields, info)
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
            elif output_format == "jsonl":
                writer = JsonLinesWriter(output_handle)
            else:
                writer = StixBundleWriter(output_handle, output["stix"])

        expanded_inputs = []
        for source in config["inputs"]:
            paths = expand_input_paths(config, source["path"])
            source_kind = input_format(source)
            stats["input_patterns"].append({
                "path": source["path"], "format": source_kind, "matched_files": len(paths),
            })
            if info:
                info(
                    f"Input {source['path']!r} matched {len(paths)} file(s) "
                    f"as {source_kind.upper()}."
                )
            expanded_inputs.extend((source, path) for path in paths)
        for source, path in expanded_inputs:
            source_stats: Counter[str] = Counter()
            reader_stats: dict[str, Any] = {}
            for source_row_number, row in read_rows(source, path, fields, reader_stats, info):
                if output.get("add_provenance", False):
                    row["source_file"] = str(path)
                    row["source_row"] = str(source_row_number)
                handle_normalized_row(
                    row, path, source_row_number, config, validation, validation_rules,
                    excluder, dedupe, writer, reject_writer, stats, source_stats,
                )
            stats["rows_read"] += reader_stats.get("rows_read", 0)
            stats["rows_skipped"] += reader_stats.get("rows_skipped", 0)
            for key, value in source_stats.items():
                reader_stats[key] = reader_stats.get(key, 0) + value
            stats["files"].append(reader_stats)
        if output_handle:
            if isinstance(writer, (JsonArrayWriter, JsonLinesWriter, StixBundleWriter)):
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
        if excluder:
            excluder.close()
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


__all__ = ["inspect_file", "process", "write_json"]

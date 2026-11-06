"""Presence comparison using shared readers and a temporary disk-backed index."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from .config import output_columns, validate_config
from .errors import ConfigError, RowError
from .mapping import input_format, mapping_parts
from .paths import config_path, expand_input_paths
from .processing import apply_transformations
from .readers import read_rows
from .writers import open_output


def side_column(config: dict[str, Any]) -> str | None:
    default = "PresentIn" if config["comparison"].get("mode", "both") == "both" else None
    return config["output"].get("side_column", default)


def validate_comparison(config: dict[str, Any]) -> list[str]:
    """Validate both sources and comparison-specific options without reading rows."""
    unexpected = set(config) - {"left", "right", "comparison", "output", "transformations", "_config_directory"}
    if unexpected:
        raise ConfigError(f"Unsupported comparison sections: {', '.join(sorted(unexpected))}.")
    options = config.get("comparison")
    if not isinstance(options, dict):
        raise ConfigError("comparison must be an object.")
    unknown = set(options) - {"keys", "mode", "allow_empty_keys", "on_empty_key"}
    if unknown:
        raise ConfigError(f"Unsupported comparison options: {', '.join(sorted(unknown))}.")
    mode = options.get("mode", "both")
    if mode not in ("left_only", "right_only", "both"):
        raise ConfigError("comparison.mode must be 'left_only', 'right_only', or 'both'.")
    if not isinstance(options.get("allow_empty_keys", False), bool):
        raise ConfigError("comparison.allow_empty_keys must be true or false.")
    if options.get("on_empty_key", "error") not in ("error", "skip"):
        raise ConfigError("comparison.on_empty_key must be 'error' or 'skip'.")
    if options.get("allow_empty_keys", False) and options.get("on_empty_key", "error") != "error":
        raise ConfigError("on_empty_key: skip cannot be combined with allow_empty_keys: true.")
    fields = list(output_columns(config))
    keys = options.get("keys")
    if (
        not isinstance(keys, list) or not keys
        or not all(isinstance(key, str) and key in config["output"]["columns"] for key in keys)
        or len(set(keys)) != len(keys)
    ):
        raise ConfigError("comparison.keys must be unique, non-empty names from output.columns.")
    label = side_column(config)
    if label is not None and (not isinstance(label, str) or not label or label in fields):
        raise ConfigError("output.side_column must be a non-empty name distinct from output and provenance columns.")
    if mode == "both" and label is None:
        raise ConfigError("output.side_column cannot be null when comparison.mode is 'both'.")
    if label:
        fields.append(label)
    sources = []
    for side in ("left", "right"):
        source = config.get(side)
        if not isinstance(source, dict):
            raise ConfigError(f"{side} must be one input specification object.")
        if "transformations" in source:
            raise ConfigError("Use top-level transformations to normalize both comparison sides.")
        sources.append(source)
    # Reuse the same input/output/mapping/action validation as merge jobs.
    validation_job = {
        "_config_directory": config.get("_config_directory", str(Path.cwd())),
        "output": {**config["output"], "columns": fields, "add_provenance": False},
        "inputs": sources,
        "transformations": config.get("transformations", {}),
    }
    validate_config(validation_job)
    if label in config.get("transformations", {}):
        raise ConfigError("The generated side column cannot be transformed.")
    for side, source in zip(("left", "right"), sources):
        rules, constants = mapping_parts(source)
        targets = {rule.output_column for rule in rules} | set(constants)
        if label and label in targets:
            raise ConfigError(f"{side}.mapping cannot populate the generated side column.")
        if not set(keys) <= targets:
            raise ConfigError(f"{side}.mapping must supply every comparison key.")
    if config["output"].get("format", "csv") == "stix" and label:
        property_name = config["output"]["stix"].get("properties", {}).get(label)
        if not isinstance(property_name, str) or not property_name.startswith("x_"):
            raise ConfigError("STIX comparison output must map output.side_column to an x_ custom property.")
    destination = config_path(config, config["output"]["path"])
    for source in sources:
        for path in expand_input_paths(config, source["path"]):
            if destination.resolve() == path.resolve() or (
                destination.exists() and destination.samefile(path)
            ):
                raise ConfigError("Comparison output must not overwrite either input file.")
    return fields


def compare(
    config: dict[str, Any], dry_run: bool = False, info: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Compare composite-key membership; preserve unmatched rows and duplicates."""
    fields = validate_comparison(config)
    options = config["comparison"]
    mode = options.get("mode", "both")
    keys = options["keys"]
    label = side_column(config)
    destination = config_path(config, config["output"]["path"])
    report: dict[str, Any] = {
        "operation": "compare", "mode": mode, "keys": keys, "output": str(destination),
        "dry_run": dry_run, "rows_written": 0,
    }
    with tempfile.TemporaryDirectory(prefix="mosaic-compare-") as temporary:
        with closing(sqlite3.connect(str(Path(temporary) / "records.sqlite3"))) as db:
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-8192")
            db.execute("PRAGMA temp_store=FILE")
            db.execute(
                "CREATE TABLE records (side TEXT NOT NULL, sequence INTEGER NOT NULL, "
                "key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(side, sequence))"
            )
            for side in ("left", "right"):
                source = config[side]
                paths = expand_input_paths(config, source["path"])
                summary = {
                    "path": source["path"], "format": input_format(source), "matched_files": len(paths),
                    "rows_read": 0, "rows_skipped": 0, "rows_invalid_keys": 0,
                    "rows_matched": 0, "rows_missing": 0, "rows_written": 0, "files": [],
                }
                report[side] = summary
                if info:
                    info(f"Comparison {side}: {source['path']!r} matched {len(paths)} file(s).")
                sequence = 0
                for path in paths:
                    file_stats: dict[str, Any] = {}
                    for number, row in read_rows(source, path, fields, file_stats, info):
                        if config["output"].get("add_provenance", False):
                            row["source_file"], row["source_row"] = str(path), str(number)
                        try:
                            apply_transformations(row, config)
                        except RowError as error:
                            raise RowError(f"{side} {path}:{number}: {error}") from error
                        values = [row[key] for key in keys]
                        if not options.get("allow_empty_keys", False) and any(value == "" for value in values):
                            if options.get("on_empty_key", "error") == "error":
                                raise RowError(f"{side} {path}:{number}: comparison key contains an empty value.")
                            file_stats["rows_skipped"] = file_stats.get("rows_skipped", 0) + 1
                            file_stats["rows_invalid_keys"] = file_stats.get("rows_invalid_keys", 0) + 1
                            continue
                        sequence += 1
                        if label:
                            row[label] = side
                        selected = mode in ("both", side + "_only")
                        payload = json.dumps(row, ensure_ascii=False, separators=(",", ":")) if selected and not dry_run else ""
                        db.execute("INSERT INTO records VALUES (?, ?, ?, ?)", (
                            side, sequence, json.dumps(values, ensure_ascii=False, separators=(",", ":")), payload,
                        ))
                    for counter in ("rows_read", "rows_skipped", "rows_invalid_keys"):
                        summary[counter] += file_stats.get(counter, 0)
                    summary["files"].append(file_stats)
            db.execute("CREATE INDEX record_keys ON records(side, key)")
            db.commit()
            missing_sql = (
                "SELECT a.payload FROM records a WHERE a.side = ? AND NOT EXISTS "
                "(SELECT 1 FROM records b WHERE b.side = ? AND b.key = a.key) ORDER BY a.sequence"
            )
            # Compute both sides' statistics even for a one-direction output.
            for side, opposite in (("left", "right"), ("right", "left")):
                summary = report[side]
                missing = db.execute(
                    "SELECT COUNT(*) FROM records a WHERE a.side = ? AND NOT EXISTS "
                    "(SELECT 1 FROM records b WHERE b.side = ? AND b.key = a.key)", (side, opposite),
                ).fetchone()[0]
                summary["rows_missing"] = missing
                summary["rows_matched"] = summary["rows_read"] - summary["rows_skipped"] - missing
            with open_output(destination, config["output"], fields, dry_run) as writer:
                for side, opposite in (("left", "right"), ("right", "left")):
                    if mode not in ("both", side + "_only"):
                        continue
                    for (payload,) in db.execute(missing_sql, (side, opposite)):
                        if writer:
                            writer.writerow(json.loads(payload))
                        report[side]["rows_written"] += 1
                        report["rows_written"] += 1
            if info:
                info(f"Comparison complete: {report['rows_written']} unmatched row(s).")
    return report

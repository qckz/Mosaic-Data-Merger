"""Temporary disk-backed stores for deduplication and exclusion keys."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .csvio import csv_options, detect_dialect
from .errors import ConfigError, RowError
from .mapping import make_index_mapping, resolve_header
from .paths import expand_input_paths


class DiskDeduplicator:
    """A bounded-memory key set backed by a temporary SQLite database."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        fd, name = tempfile.mkstemp(prefix="mosaic-data-merger-dedupe-", suffix=".sqlite3")
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


class DiskExcluder:
    """Disk-backed composite-key exclusion sets, one set per configured list."""

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = rules
        fd, name = tempfile.mkstemp(prefix="mosaic-data-merger-exclusions-", suffix=".sqlite3")
        os.close(fd)
        self.path = name
        self.connection = sqlite3.connect(name)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute(
            "CREATE TABLE excluded (list_id INTEGER NOT NULL, key TEXT NOT NULL, PRIMARY KEY (list_id, key))"
        )

    @staticmethod
    def key(values: list[str]) -> str:
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    def add(self, list_id: int, values: list[str]) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO excluded(list_id, key) VALUES (?, ?)",
            (list_id, self.key(values)),
        )
        return cursor.rowcount == 1

    def matching_list(self, row: dict[str, str]) -> int | None:
        for list_id, rule in enumerate(self.rules):
            values = [row[column] for column in rule["keys"]]
            if not rule.get("allow_empty_keys", False) and any(value == "" for value in values):
                continue
            found = self.connection.execute(
                "SELECT 1 FROM excluded WHERE list_id = ? AND key = ?",
                (list_id, self.key(values)),
            ).fetchone()
            if found:
                return list_id
        return None

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()
        Path(self.path).unlink(missing_ok=True)


def load_exclusions(
    config: dict[str, Any], fields: list[str], info: Callable[[str], None] | None
) -> tuple[DiskExcluder | None, list[dict[str, Any]]]:
    rules = config.get("exclusions", [])
    if not rules:
        return None, []
    excluder = DiskExcluder(rules)
    summaries: list[dict[str, Any]] = []
    try:
        for list_id, rule in enumerate(rules):
            paths = expand_input_paths(config, rule["path"])
            summary: dict[str, Any] = {
                "path": rule["path"], "keys": rule["keys"], "matched_files": len(paths),
                "rows_read": 0, "keys_loaded": 0, "duplicate_keys": 0,
                "rows_skipped": 0, "empty_keys_skipped": 0, "rows_excluded": 0,
            }
            if info:
                info(f"Exclusion list {rule['path']!r} matched {len(paths)} file(s).")
            for path in paths:
                encoding = rule.get("encoding", "utf-8")
                delimiter = rule.get("delimiter")
                if delimiter is None:
                    delimiter, _ = detect_dialect(path, encoding)
                options = csv_options(rule, delimiter)
                has_header = resolve_header(rule, path, encoding, options)
                if info:
                    info(
                        f"Loading exclusions from {path}: CSV delimiter={options['delimiter']!r}, "
                        f"header={'yes' if has_header else 'no'}."
                    )
                with path.open("r", encoding=encoding, newline="") as handle:
                    reader = csv.reader(handle, **options)
                    header = next(reader, None) if has_header else None
                    if has_header and header is None:
                        raise ConfigError(f"Exclusion list {path} declares a header but is empty.")
                    index_mapping = make_index_mapping(rule, rule["mapping"], header, fields, options["delimiter"])
                    max_index = max((index for index, _ in index_mapping), default=-1)
                    for row_number, values in enumerate(reader, start=2 if has_header else 1):
                        summary["rows_read"] += 1
                        if len(values) <= max_index:
                            policy = rule.get("on_malformed_row", "error")
                            message = (
                                f"exclusion row parsed as {len(values)} column(s) with delimiter "
                                f"{options['delimiter']!r}; mapping requires column {max_index + 1}."
                            )
                            if policy == "error":
                                raise RowError(f"{path}:{row_number}: {message}")
                            if policy == "skip":
                                summary["rows_skipped"] += 1
                                continue
                        exclusion_row = {field: "" for field in fields}
                        for source_index, target in index_mapping:
                            exclusion_row[target] = values[source_index] if source_index < len(values) else ""
                        key_values = [exclusion_row[column] for column in rule["keys"]]
                        if not rule.get("allow_empty_keys", False) and any(value == "" for value in key_values):
                            summary["empty_keys_skipped"] += 1
                            continue
                        if excluder.add(list_id, key_values):
                            summary["keys_loaded"] += 1
                        else:
                            summary["duplicate_keys"] += 1
            summaries.append(summary)
        return excluder, summaries
    except Exception:
        excluder.close()
        raise

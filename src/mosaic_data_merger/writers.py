"""Streaming output writers and atomic destination management."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .csvio import output_csv_options
from .errors import ConfigError


@contextmanager
def open_output(destination: Path, output: dict[str, Any], fields: list[str], dry_run: bool = False):
    """Open a shared format writer; publish atomic replacements only on success."""
    encoding = output.get("encoding", "utf-8")
    options = output_csv_options(output)
    kind = output.get("format", "csv")
    mode = output.get("mode", "replace")
    populated = destination.exists() and destination.stat().st_size > 0
    if kind == "csv" and mode == "append" and populated:
        with destination.open("r", encoding=encoding, newline="") as existing:
            if next(csv.reader(existing, **options), None) != fields:
                raise ConfigError("Cannot append: the existing output header differs from output.columns.")
    if kind == "jsonl" and mode == "append" and populated:
        # Check using the configured encoding; avoid concatenating two JSON records.
        with destination.open("r", encoding=encoding, newline="") as existing:
            last = ""
            for line in existing:
                last = line
            if not last.endswith(("\n", "\r")):
                raise ConfigError("Cannot append JSONL: the existing output must end with a newline.")
    if dry_run:
        yield None
        return
    temporary = None
    handle = None
    try:
        target = destination
        if mode == "replace" and output.get("atomic_write", True):
            target, temporary = atomic_path(destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a" if mode == "append" else "w", encoding=encoding, newline="")
        if kind == "csv":
            writer = csv.DictWriter(handle, fieldnames=fields, **options)
            if mode == "replace" or not populated:
                writer.writeheader()
        elif kind == "json":
            writer = JsonArrayWriter(handle)
        elif kind == "jsonl":
            writer = JsonLinesWriter(handle)
        else:
            writer = StixBundleWriter(handle, output["stix"])
        yield writer
        if kind != "csv":
            writer.close()
        else:
            handle.close()
        if temporary:
            os.replace(temporary, destination)
            temporary = None
    finally:
        if handle:
            handle.close()
        if temporary:
            temporary.unlink(missing_ok=True)


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


class JsonLinesWriter:
    """Write one normalized JSON object per line, suitable for append mode."""

    def __init__(self, handle: Any) -> None:
        self.handle = handle

    def writerow(self, row: dict[str, str]) -> None:
        json.dump(row, self.handle, ensure_ascii=False, separators=(",", ":"))
        self.handle.write("\n")

    def close(self) -> None:
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


def atomic_path(destination: Path) -> tuple[Path, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    return Path(temporary_name), Path(temporary_name)

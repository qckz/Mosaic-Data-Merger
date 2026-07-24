"""CSV dialect detection and reader/writer option handling."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .errors import ConfigError


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


def output_csv_options(output: dict[str, Any]) -> dict[str, Any]:
    options = csv_options(output, ",")
    quote_all = output.get("quote_all", False)
    if not isinstance(quote_all, bool):
        raise ConfigError("output.quote_all must be true or false.")
    if quote_all:
        if output.get("quotechar", '"') != '"':
            raise ConfigError("output.quote_all requires the double-quote quotechar.")
        if output.get("doublequote", True) is not True:
            raise ConfigError("output.quote_all requires doublequote: true to escape embedded double quotes.")
        options["quotechar"] = '"'
        options["doublequote"] = True
        options["quoting"] = csv.QUOTE_ALL
    return options

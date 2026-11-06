"""Command-line interface for Mosaic Data Merger."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from pathlib import Path

from .config import load_config, validate_config
from .comparison import compare, validate_comparison
from .errors import ConfigError, RowError
from .mapping import input_format
from .paths import config_path, expand_input_paths
from .pipeline import inspect_file, process, write_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge, transform, or compare CSV, JSON, JSONL, and STIX files."
    )
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
    run_parser.add_argument("--verbose", action="store_true", help="Write per-input progress information to stderr.")
    compare_parser = subparsers.add_parser("compare", help="Write records missing from the opposite comparison side.")
    compare_parser.add_argument("--config", required=True, type=Path)
    compare_parser.add_argument("--dry-run", action="store_true", help="Compare and report without producing data output.")
    compare_parser.add_argument("--report", help="Save the comparison summary as JSON.")
    compare_parser.add_argument("--verbose", action="store_true", help="Write comparison progress to stderr.")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        if args.command == "inspect":
            if not args.path.is_file():
                raise ConfigError(f"Input file does not exist: {args.path}")
            write_json(inspect_file(args.path, args.encoding, args.delimiter, args.format))
        elif args.command == "validate":
            config = load_config(args.config)
            if any(key in config for key in ("comparison", "left", "right")):
                fields = validate_comparison(config)
                write_json({
                    "valid": True, "operation": "compare", "output_columns": fields,
                    "mode": config["comparison"].get("mode", "both"), "keys": config["comparison"]["keys"],
                    "input_patterns": [
                        {
                            "side": side, "path": config[side]["path"],
                            "format": input_format(config[side]),
                            "matched_files": len(expand_input_paths(config, config[side]["path"])),
                        }
                        for side in ("left", "right")
                    ],
                })
                return 0
            fields = validate_config(config)
            write_json({
                "valid": True,
                "output_columns": fields,
                "input_count": len(config["inputs"]),
                "input_patterns": [
                    {
                        "path": source["path"],
                        "format": input_format(source),
                        "matched_files": len(expand_input_paths(config, source["path"])),
                    }
                    for source in config["inputs"]
                ],
                "exclusion_patterns": [
                    {
                        "path": exclusion["path"],
                        "keys": exclusion["keys"],
                        "matched_files": len(expand_input_paths(config, exclusion["path"])),
                    }
                    for exclusion in config.get("exclusions", [])
                ],
            })
        else:
            config = load_config(args.config)
            if args.command == "compare":
                validate_comparison(config)
                destination = config_path(config, config["output"]["path"])
                if destination.resolve() == args.config.resolve() or (
                    destination.exists() and destination.samefile(args.config)
                ):
                    raise ConfigError("Comparison output must not overwrite the configuration file.")
            if args.command == "compare" and args.report:
                report_path = Path(args.report)
                protected = [args.config, config_path(config, config["output"]["path"])]
                for side in ("left", "right"):
                    protected.extend(expand_input_paths(config, config[side]["path"]))
                if any(report_path.resolve() == path.resolve() or (
                    report_path.exists() and path.exists() and report_path.samefile(path)
                ) for path in protected):
                    raise ConfigError("--report must not overwrite the config, inputs, or data output.")
            operation = compare if args.command == "compare" else process
            report = operation(
                config,
                dry_run=args.dry_run,
                info=(lambda message: print(f"info: {message}", file=sys.stderr)) if args.verbose else None,
            )
            report["duration_seconds"] = round(time.monotonic() - started, 3)
            write_json(report)
            if args.report:
                write_json(report, args.report)
        return 0
    except (ConfigError, RowError, OSError, csv.Error, UnicodeError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

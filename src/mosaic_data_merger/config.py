"""Load and validate Mosaic Data Merger job configurations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .csvio import csv_options, output_csv_options
from .errors import ConfigError
from .mapping import input_format, mapping_parts
from .paths import expand_input_paths


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
    config["_config_directory"] = str(path.resolve().parent)
    return config


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
    if output_format not in {"csv", "json", "jsonl", "stix"}:
        raise ConfigError("output.format must be 'csv', 'json', 'jsonl', or 'stix'.")
    if output_format != "csv" and output.get("quote_all", False):
        raise ConfigError("output.quote_all is available only for CSV output.")
    output_csv_options(output)
    if output.get("mode", "replace") not in {"replace", "append"}:
        raise ConfigError("output.mode must be 'replace' or 'append'.")
    if output_format not in {"csv", "jsonl"} and output.get("mode", "replace") == "append":
        raise ConfigError("output.mode 'append' is available only for CSV or JSONL output.")
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
        if any(
            column not in columns or not isinstance(property_name, str) or not property_name
            for column, property_name in properties.items()
        ):
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
        if check_inputs:
            expand_input_paths(config, path)
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
        constants = mapping.get("$constants", {})
        if not isinstance(constants, dict):
            raise ConfigError(f"inputs[{index}].mapping.$constants must be an object.")
        source_mapping, constants = mapping_parts(source)
        if not source_mapping and not constants:
            raise ConfigError(f"inputs[{index}].mapping must map a source field or define $constants.")
        targets = list(source_mapping.values())
        if not all(isinstance(target, str) and target in columns for target in targets):
            raise ConfigError(f"inputs[{index}].mapping targets must be names in output.columns.")
        if not all(isinstance(target, str) and target in columns for target in constants):
            raise ConfigError(f"inputs[{index}].mapping.$constants keys must be names in output.columns.")
        if len(set([*targets, *constants])) != len([*targets, *constants]):
            raise ConfigError(f"inputs[{index}] maps more than one value to an output field.")
        for source_column in source_mapping:
            if not isinstance(source_column, str) or not source_column:
                raise ConfigError(f"inputs[{index}] has an invalid mapping key.")
            if source_kind == "csv" and source_column.isdigit() and int(source_column) < 1:
                raise ConfigError("Numeric source column mappings are 1-based (minimum 1).")
        if source.get("on_malformed_row", "error") not in {"error", "skip", "pad"}:
            raise ConfigError("on_malformed_row must be 'error', 'skip', or 'pad'.")
        if source_kind in {"jsonl", "json", "stix"} and source.get("on_malformed_row") == "pad":
            raise ConfigError(
                f"{source_kind.upper()} inputs support on_malformed_row 'error' or 'skip', not 'pad'."
            )

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

    exclusions = config.get("exclusions", [])
    if not isinstance(exclusions, list) or not all(isinstance(exclusion, dict) for exclusion in exclusions):
        raise ConfigError("exclusions must be a list of exclusion-list objects.")
    for index, exclusion in enumerate(exclusions, start=1):
        path = exclusion.get("path")
        if not isinstance(path, str) or not path:
            raise ConfigError(f"exclusions[{index}].path must be a non-empty path.")
        if check_inputs:
            expand_input_paths(config, path)
        if exclusion.get("format", "csv") != "csv":
            raise ConfigError("Exclusion lists currently support only CSV format.")
        header = exclusion.get("header", True)
        if header not in {True, False, "auto"}:
            raise ConfigError(f"exclusions[{index}].header must be true, false, or 'auto'.")
        if "delimiter" in exclusion:
            csv_options(exclusion)
        mapping = exclusion.get("mapping")
        if not isinstance(mapping, dict) or not mapping or "$constants" in mapping:
            raise ConfigError(f"exclusions[{index}].mapping must be a non-empty source-field mapping.")
        if not all(
            isinstance(source_column, str) and source_column
            and isinstance(target, str) and target in columns
            for source_column, target in mapping.items()
        ):
            raise ConfigError(f"exclusions[{index}].mapping must map source fields to output.columns.")
        for source_column in mapping:
            if source_column.isdigit() and int(source_column) < 1:
                raise ConfigError("Numeric exclusion column mappings are 1-based (minimum 1).")
        keys = exclusion.get("keys")
        if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and key in columns for key in keys):
            raise ConfigError(f"exclusions[{index}].keys must be non-empty output column names.")
        if len(set(keys)) != len(keys):
            raise ConfigError(f"exclusions[{index}].keys contains duplicate column names.")
        if any(key not in mapping.values() for key in keys):
            raise ConfigError(f"exclusions[{index}].keys must each be mapped by exclusions[{index}].mapping.")
        if exclusion.get("on_malformed_row", "error") not in {"error", "skip", "pad"}:
            raise ConfigError("exclusion on_malformed_row must be 'error', 'skip', or 'pad'.")
        if not isinstance(exclusion.get("allow_empty_keys", False), bool):
            raise ConfigError("exclusion allow_empty_keys must be true or false.")

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

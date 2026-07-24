# Changelog

All notable changes to Mosaic Data Merger are documented here.

## 0.5.5

- Added descriptive optional mappings with `output_column` and `default_if_missing`, so CSV, JSON, JSONL, and STIX sources can use a fallback when a source field does not exist.
- Renamed the empty-value transformation from `set_default` to `default_if_empty`. The old name is no longer accepted.

## 0.5.0

This is the first public, pre-1.0 feature release of Mosaic Data Merger.

### Input and output formats

- Read CSV files with explicit, detected, or automatic header handling; source columns may be mapped by header name or one-based position.
- Read JSONL streams, JSON objects or arrays, and STIX 2.1 Bundles or individual STIX objects.
- Merge mixed CSV, JSON, JSONL, and STIX inputs into one result, or transform one input file by itself.
- Write CSV, JSON arrays, JSONL streams, or STIX 2.1 Bundles.
- Configure STIX output object types, property mappings, static properties, Bundle IDs, and timestamps; generated objects receive STIX IDs and timestamps by default.
- Support CSV delimiters, encodings, quote and escape settings, strict parsing, and fully quoted output with standard double-quote escaping.
- Support replace and append modes where appropriate; CSV and JSONL can append, while JSON and STIX use replacement output.
- Use atomic replacement output by default to avoid exposing partial files to downstream automation.

### Mapping and normalization

- Define an ordered output schema and map every supported source format into it.
- Map CSV fields by name or one-based position and JSON/STIX properties by key.
- Map several source layouts to the same output columns across one job.
- Fill absent source fields with `$constants`, including string, number, boolean, null, array, and object values.
- Optionally add `source_file` and `source_row` provenance fields.

### Transformations, filters, and validation

- Transform values with `trim`, `lowercase`, `uppercase`, `title_case`, `set_default`, `replace`, `null_if`, and `date_format`.
- Filter rows with `not_empty`, `empty`, `equals`, `not_equals`, `contains`, `in`, and `regex` conditions.
- Validate required, date, numeric, and regular-expression rules.
- Stop, skip, or write invalid normalized rows to a rejects CSV with an error explanation.
- Handle malformed CSV rows using `error`, `skip`, or `pad`; JSON, JSONL, and STIX invalid-record policies support `error` or `skip`.

### Large-file and workflow features

- Process CSV and JSONL input incrementally rather than loading all records into memory.
- Use disk-backed SQLite indexes for scalable deduplication and exclusion lists.
- Exclude rows with one or more CSV lists using complete composite-key (AND) matching.
- Deduplicate by configured output keys while retaining the first matching row.
- Expand wildcard input and exclusion paths (`*`, `?`, character sets, and recursive `**`) for every supported input format.
- Report per-file and total read, written, filtered, rejected, skipped, excluded, and deduplicated counts.
- Provide verbose progress messages and delimiter/header diagnostics to make automation failures easier to diagnose.

### CLI, documentation, and architecture

- Provide the packaged `mosaic-data-merger` command and `python -m mosaic_data_merger` entry point.
- Provide `inspect`, `validate`, and `run` commands, including dry runs and optional JSON report files.
- Publish concise quick-start documentation, a full configuration reference, architecture documentation, sample jobs, sample data, and a configuration catalog.
- Restructure the implementation into focused modules for configuration, paths, CSV I/O, mappings, row processing, temporary stores, writers, pipeline orchestration, and CLI handling.

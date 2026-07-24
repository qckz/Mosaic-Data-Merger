# CLI output and report reference

Mosaic Data Merger is designed for automation. Every successful command writes one JSON document to standard output. This reference defines the fields in those documents and explains how to interpret them.

## Output contract

Successful "inspect", "validate", and "run" commands:

- return exit code 0;
- write one valid UTF-8 JSON document to standard output;
- use indented JSON for readability;
- keep standard output free of progress messages.

This makes standard output safe to capture and parse.

~~~bash
mosaic-data-merger run --config config/job.json > run-report.json
~~~

Paths inside the job configuration are relative to the configuration file. The optional "run --report PATH" argument is different: a relative report path is relative to the shell's current directory.

## Contents

- [Inspect output](#inspect-output)
- [Validate output](#validate-output)
- [Run output](#run-output)
- [Per-file statistics](#per-file-statistics)
- [Input and exclusion patterns](#input-and-exclusion-patterns)
- [Errors and verbose diagnostics](#errors-and-verbose-diagnostics)
- [Automation examples](#automation-examples)

## Inspect output

~~~bash
mosaic-data-merger inspect input/people.csv
mosaic-data-merger inspect input/events.jsonl --format jsonl
mosaic-data-merger inspect input/intelligence.json --format stix
~~~

"inspect" accepts one physical file, not a wildcard. It does not transform or write data. Its result depends on the selected or inferred format.

### CSV

~~~json
{
  "path": "input/people.csv",
  "encoding": "utf-8",
  "format": "csv",
  "detected_delimiter": ",",
  "delimiter_used": ",",
  "header_likely": true,
  "sample_rows": [
    ["City", "Name"],
    ["Amsterdam", "Alice"]
  ]
}
~~~

| Field | Type | Meaning |
|---|---|---|
| "path" | string | File path supplied to "inspect". |
| "encoding" | string | Encoding used to read the file. |
| "format" | "csv" | Inspected format. |
| "detected_delimiter" | string | Separator detected from a CSV sample. |
| "delimiter_used" | string | Separator used for "sample_rows"; this is "--delimiter" when supplied, otherwise the detected delimiter. |
| "header_likely" | boolean | CSV heuristic only. It is a hint, not a guarantee. Configure "header" explicitly for production jobs. |
| "sample_rows" | array of arrays | Up to the first five parsed CSV rows, including a header row when present. |

### JSONL

~~~json
{
  "path": "input/events.jsonl",
  "encoding": "utf-8",
  "format": "jsonl",
  "sample_keys": ["city", "event"],
  "sample_rows": [
    {"city": "Amsterdam", "event": "login"}
  ]
}
~~~

| Field | Type | Meaning |
|---|---|---|
| "sample_keys" | array of strings | Sorted union of top-level keys found in up to the first five JSONL records. |
| "sample_rows" | array of objects | Up to five parsed JSONL objects. |

JSONL records must be JSON objects. An invalid line causes "inspect" to fail and reports its line number.

### JSON

~~~json
{
  "path": "input/people.json",
  "encoding": "utf-8",
  "format": "json",
  "record_count": 2,
  "sample_keys": ["city", "name"],
  "sample_rows": [
    {"city": "Amsterdam", "name": "Alice"}
  ]
}
~~~

| Field | Type | Meaning |
|---|---|---|
| "record_count" | integer | Number of source objects: one top-level object or the length of a top-level array. |
| "sample_keys" | array of strings | Sorted union of keys from the first five objects. |
| "sample_rows" | array of objects | Up to five source objects. |

### STIX

~~~json
{
  "path": "input/intelligence.json",
  "encoding": "utf-8",
  "format": "stix",
  "bundle_id": "bundle--00000000-0000-4000-8000-000000000001",
  "object_count": 1,
  "sample_keys": ["id", "name", "type"],
  "sample_rows": [
    {"type": "indicator", "id": "indicator--..."}
  ]
}
~~~

| Field | Type | Meaning |
|---|---|---|
| "bundle_id" | string or null | Bundle ID for a STIX Bundle; null for one STIX object. |
| "object_count" | integer | Number of Bundle objects, or one for an individual STIX object. |
| "sample_keys", "sample_rows" | arrays | Same meaning as JSON inspection, applied to STIX objects. |

## Validate output

~~~bash
mosaic-data-merger validate --config config/job.json
~~~

"validate" checks configuration values, resolves input and exclusion paths, expands wildcard patterns, and verifies mapping targets. It does not read every source record or write output.

~~~json
{
  "valid": true,
  "output_columns": ["Place", "Name", "source_file", "source_row"],
  "input_count": 2,
  "input_patterns": [
    {
      "path": "../input/people-*.csv",
      "format": "csv",
      "matched_files": 60
    }
  ],
  "exclusion_patterns": [
    {
      "path": "../input/excluded.csv",
      "keys": ["Email", "Country"],
      "matched_files": 1
    }
  ]
}
~~~

| Field | Type | Meaning |
|---|---|---|
| "valid" | boolean | Always true for a successful validation. Invalid configuration returns an error instead. |
| "output_columns" | array of strings | Final ordered output schema. It includes "source_file" and "source_row" when provenance is enabled. |
| "input_count" | integer | Number of input specifications before wildcard expansion. |
| "input_patterns" | array | One entry for each input specification. |
| "exclusion_patterns" | array | One entry for each exclusion specification. |

## Run output

~~~bash
mosaic-data-merger run --config config/job.json
mosaic-data-merger run --config config/job.json --dry-run
mosaic-data-merger run --config config/job.json --report reports/latest.json
~~~

"run" returns a complete operational report for CSV, JSON, JSONL, and STIX output.

~~~json
{
  "output": "/absolute/path/output/people.csv",
  "rows_read": 100,
  "rows_written": 88,
  "rows_filtered": 4,
  "rows_rejected": 2,
  "rows_skipped": 1,
  "rows_excluded": 3,
  "rows_deduplicated": 2,
  "files": [],
  "input_patterns": [],
  "exclusions": [],
  "dry_run": false,
  "duration_seconds": 1.237
}
~~~

### Top-level report fields

| Field | Type | Meaning |
|---|---|---|
| "output" | string | Absolute resolved output destination. It is reported even during a dry run. |
| "rows_read" | integer | Source records examined across every expanded input file. |
| "rows_written" | integer | Records written, or records that would be written during a dry run. |
| "rows_filtered" | integer | Records removed because at least one filter did not match. |
| "rows_rejected" | integer | Validation failures written to the rejects file. |
| "rows_skipped" | integer | Malformed or invalid records skipped by policy, including validation failures with "on_error": "skip". |
| "rows_excluded" | integer | Records removed because they matched an exclusion list. |
| "rows_deduplicated" | integer | Records removed because they matched an earlier configured deduplication key. |
| "files" | array | Per-expanded-input-file statistics. |
| "input_patterns" | array | One report entry for each configured input specification. |
| "exclusions" | array | One loading and matching report entry for each exclusion list. |
| "dry_run" | boolean | True when "--dry-run" was used. |
| "duration_seconds" | number | Wall-clock duration added by the CLI. The Python "process()" API does not add this field. |

Top-level counters are always present, including zero values.

### Counting rules

Every source record increments "rows_read" once. It can then end in one of these outcomes:

| Outcome | Counter | When it happens |
|---|---|---|
| Written | "rows_written" | Record passes all stages, or would pass in a dry run. |
| Filtered | "rows_filtered" | At least one filter does not match. |
| Excluded | "rows_excluded" | Record matches an exclusion-list key. |
| Rejected | "rows_rejected" | Validation fails and "validation.on_error" is "reject". |
| Skipped | "rows_skipped" | A malformed/invalid record is skipped, or validation fails with "on_error": "skip". |
| Deduplicated | "rows_deduplicated" | Record matches an earlier deduplication key. |

With an "error" policy, processing stops instead of returning a completed report.

### Dry runs

A dry run performs mapping, transformations, filters, exclusions, validation, and deduplication, but creates no output or rejects files. "rows_written" means rows that would have been written.

## Per-file statistics

"files" preserves processing order: input-specification order, then lexical wildcard-match order.

Every entry contains:

| Field | Type | Meaning |
|---|---|---|
| "path" | string | Absolute path to the physical input file. |
| "format" | string | Effective format: "csv", "json", "jsonl", or "stix". |

CSV entries also contain:

| Field | Type | Meaning |
|---|---|---|
| "header_used" | boolean | Whether this file was processed with a header. |
| "delimiter" | string | Effective one-character CSV delimiter. |

The row counters described above appear in an individual file entry only when their value is non-zero. A missing per-file counter means zero, not unknown.

~~~json
{
  "path": "/absolute/path/input/people-01.csv",
  "rows_read": 500000,
  "rows_written": 496100,
  "rows_excluded": 3100,
  "rows_deduplicated": 800,
  "format": "csv",
  "header_used": true,
  "delimiter": ";"
}
~~~

## Input and exclusion patterns

Both "validate" and "run" include "input_patterns":

~~~json
{
  "path": "../input/people-*.csv",
  "format": "csv",
  "matched_files": 60
}
~~~

| Field | Type | Meaning |
|---|---|---|
| "path" | string | Exact configured input path, including a wildcard when used. |
| "format" | string | Configured or inferred input format. |
| "matched_files" | integer | Number of matching regular files; one for an ordinary file. |

"validate" uses "exclusion_patterns", which verifies paths and configured keys:

~~~json
{
  "path": "../input/do-not-export-*.csv",
  "keys": ["Email", "Country"],
  "matched_files": 2
}
~~~

"run" uses "exclusions", which additionally reports list loading and matching:

~~~json
{
  "path": "../input/do-not-export-*.csv",
  "keys": ["Email", "Country"],
  "matched_files": 2,
  "rows_read": 31000,
  "keys_loaded": 30850,
  "duplicate_keys": 100,
  "rows_skipped": 20,
  "empty_keys_skipped": 30,
  "rows_excluded": 3100
}
~~~

| Exclusion field | Type | Meaning |
|---|---|---|
| "path" | string | Exact configured exclusion path. |
| "keys" | array of strings | Composite output key names. All values must match to exclude a row. |
| "matched_files" | integer | Number of expanded exclusion files. |
| "rows_read" | integer | Rows read from all matching exclusion files. |
| "keys_loaded" | integer | Unique complete keys added to the temporary exclusion index. |
| "duplicate_keys" | integer | Repeated exclusion keys ignored because they were already loaded. |
| "rows_skipped" | integer | Malformed exclusion rows skipped by policy. |
| "empty_keys_skipped" | integer | Rows ignored because a key part was empty and empty keys are not allowed. |
| "rows_excluded" | integer | Normalized input records removed because they matched this list. |

## Errors and verbose diagnostics

| Situation | Exit code | Standard output | Standard error |
|---|:---:|---|---|
| Successful command | 0 | One JSON document | Empty unless "--verbose" is used. |
| Configuration, row, file, encoding, or CSV error | 2 | No success JSON report | One line beginning with "error:". |
| Argument syntax error | 2 | Empty | Argparse usage and error text. |

"--verbose" is available with "run". It writes progress messages to standard error without changing standard output:

~~~text
info: Input '../input/people-*.csv' matched 60 file(s) as CSV.
info: Processing /absolute/path/input/people-01.csv: CSV delimiter=';', header=yes.
info: Exclusion list '../input/do-not-export.csv' matched 1 file(s).
~~~

## Automation examples

### Save the report

~~~bash
mosaic-data-merger run --config config/job.json --report reports/latest.json > reports/latest-stdout.json
~~~

The report file and redirected standard output contain the same JSON object.

### Fail when nothing was written

~~~bash
report="$(mosaic-data-merger run --config config/job.json)"
written="$(printf '%s' "$report" | jq '.rows_written')"

if [ "$written" -eq 0 ]; then
  echo "No rows were written" >&2
  exit 1
fi
~~~

### Operational metrics to retain

For scheduled jobs, retain at least:

- "duration_seconds";
- "rows_read" and "rows_written";
- each removal counter;
- per-file "path", "rows_read", and "rows_written";
- exclusion "keys_loaded" and "rows_excluded" when exclusions are configured.

For configuration field definitions and recipes, see [CONFIGURATION.md](CONFIGURATION.md).

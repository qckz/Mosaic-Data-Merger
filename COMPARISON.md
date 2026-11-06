# Comparing files

Available from version 0.5.6. Comparison finds records present in one source but missing from another. Either side can be CSV, JSON, JSONL, or STIX; the formats and field names can differ.

## Quick start

Run the bundled example:

~~~bash
mosaic-data-merger validate --config examples/example-compare-job.json
mosaic-data-merger compare --config examples/example-compare-job.json --dry-run
mosaic-data-merger compare --config examples/example-compare-job.json --report comparison-report.json --verbose
~~~

The example reads the bundled CSV asset list and JSONL inventory. It writes:

~~~csv
"ID","Name","PresentIn"
"1","web-server","left"
"4","backup-server","right"
~~~

"left" means present on the left and absent on the right. "right" means the reverse. Records with IDs 2 and 3 are in both sources and are omitted, even though ID 2 has a different name.

## Configuration

Comparison jobs use "left" and "right" instead of the merge job's "inputs" array:

~~~json
{
  "left": {
    "path": "../input/assets.csv",
    "format": "csv",
    "header": true,
    "delimiter": ",",
    "mapping": {"Asset ID": "ID", "Hostname": "Name"}
  },
  "right": {
    "path": "../input/inventory.jsonl",
    "format": "jsonl",
    "mapping": {"asset_id": "ID", "hostname": "Name"}
  },
  "comparison": {
    "keys": ["ID"],
    "mode": "both",
    "allow_empty_keys": false,
    "on_empty_key": "error"
  },
  "output": {
    "path": "../output/differences.csv",
    "format": "csv",
    "columns": ["ID", "Name"],
    "side_column": "PresentIn"
  }
}
~~~

Paths inside this configuration are relative to its file. The "--report" path is relative to your working directory.

| Section | Required | Meaning |
|---|---|---|
| "left" | Yes | One input specification, including a mapping. Its path may contain wildcards. |
| "right" | Yes | A second independent input specification. Its path may contain wildcards. |
| "comparison" | Yes | Matching keys, mode, and empty-key policy. |
| "output" | Yes | Destination, format, columns, and optional generated side label. |
| "transformations" | No | Existing column-keyed transformations, applied identically to both sides before matching. |

The usual [input and mapping options](CONFIGURATION.md#input-reference) apply on both sides: encoding, format inference, CSV dialects, headers, one-based numeric CSV mapping, malformed-record policy, constants, and missing-field defaults. Each side must map or supply a constant for every comparison key. Non-key output columns can be unmapped; they become empty strings.

Each side is one specification, not an array of unrelated layouts. Wildcard matches on that side share its mapping and format. Missing files and patterns with no matches are configuration errors.

Merge-only sections such as "inputs", "filters", "exclusions", "validation", and "deduplication" are rejected in comparison jobs. Unmatched duplicate records are preserved. Use a separate transformation job if filtering or deduplication is needed before comparison.

## Comparison options

| Option | Default | Meaning |
|---|---|---|
| "keys" | Required | Non-empty list of unique names from "output.columns". |
| "mode" | "both" | "left_only", "right_only", or "both". |
| "allow_empty_keys" | false | When true, empty key components participate in matching. |
| "on_empty_key" | "error" | When empty keys are disallowed, "error" stops the job and "skip" ignores that row. |

Do not combine "allow_empty_keys": true with "on_empty_key": "skip"; the policies conflict.

| Mode | Written records |
|---|---|
| "left_only" | Left records whose complete key is absent on the right. |
| "right_only" | Right records whose complete key is absent on the left. |
| "both" | Both sets, with all left-only rows followed by all right-only rows. |

Within each side, output preserves lexical wildcard-file order and source-record order.

### What counts as a match?

Matching uses the normalized text values in the configured keys:

- All components must match on one opposite-side record (AND, not OR).
- A single matching record is sufficient, regardless of duplicate counts.
- Differences outside the keys are ignored. This is a presence comparison, not a field-change report.
- Matching is exact and case-sensitive by default. Whitespace matters.
- JSON numbers, booleans, and nulls use Mosaic's existing text conversion. For example, JSON number 1 can match CSV text "1"; JSON null becomes an empty string.
- An empty component in a composite key triggers the empty-key policy, even when the other components are populated.
- Header rows do not count as records. JSONL blank lines are malformed records, not empty keys.

For example, keys ["ID", "Country"] match only when both values occur together. Duplicate unmatched rows remain separate output rows; a key appearing twice on the left and once on the right matches both left rows.

### Normalize both sides

Use the same transformation configuration as merge jobs:

~~~json
"transformations": {
  "ID": [
    {"type": "trim"},
    {"type": "lowercase"}
  ]
}
~~~

The transformed values are used for matching and output. A default can intentionally supply an otherwise absent key. Transformations that fail, such as an unparseable date, stop the comparison with source location details.

### Headerless CSV and composite keys

~~~json
"left": {
  "path": "../input/legacy-*.csv",
  "header": false,
  "delimiter": ";",
  "mapping": {"1": "ID", "3": "Country"}
},
"comparison": {
  "keys": ["ID", "Country"],
  "mode": "left_only"
}
~~~

The right-side mapping must also supply both ID and Country. Both columns must be listed in "output.columns".

## Output

All four output formats are supported. Existing encoding, CSV quoting, atomic-write, and STIX configuration options apply. CSV and JSONL support append; JSON and STIX require replacement.

| Option | Default | Meaning |
|---|---|---|
| "columns" | Required | The common schema, including all comparison keys. |
| "side_column" | "PresentIn" in "both" mode; omitted otherwise | A generated column containing "left" or "right". |
| "add_provenance" | false | Add "source_file" and "source_row" for the originating record. |

The side column is added automatically: do not include it in "output.columns", mappings, or transformations. Its name must not collide with data or provenance columns. It cannot be null in "both" mode; it may be null in a one-direction mode.

For STIX output, map the side label to an explicit custom property:

~~~json
"output": {
  "path": "../output/differences-stix.json",
  "format": "stix",
  "columns": ["ID", "Name"],
  "side_column": "PresentIn",
  "stix": {
    "object_type": "note",
    "properties": {
      "Name": "content",
      "ID": "x_source_id",
      "PresentIn": "x_present_in"
    },
    "static": {
      "object_refs": ["identity--00000000-0000-4000-8000-000000000001"]
    }
  }
}
~~~

Replace the illustrative reference with one from your own data. A configured side label must map to an "x_" custom property in STIX output. Existing STIX schema-validation limitations apply; see the [STIX output reference](CONFIGURATION.md#stix-output).

Output cannot overwrite either source. The CLI also protects the configuration file and prevents "--report" from overwriting the configuration, either input, or the data output.

By default, replacement output is atomic: a failed write leaves an existing destination unchanged. Append output and explicitly non-atomic output can be partial if writing fails. CSV append requires an identical existing header; JSONL append requires a trailing newline.

## Reports and automation

Successful "compare" commands print a JSON summary to stdout and exit with code 0. "--verbose" writes progress to stderr. "--report" saves the same summary as UTF-8 JSON. "--dry-run" computes the result and report without writing data output, but still uses temporary storage and saves an explicitly requested report.

| Top-level field | Meaning |
|---|---|
| "operation" | Always "compare". |
| "mode" | Effective comparison mode. |
| "keys" | Ordered comparison key names. |
| "output" | Config-relative output destination made absolute. |
| "dry_run" | Whether output writing was suppressed. |
| "rows_written" | Rows written in this run, or that would be written in a dry run. |
| "left", "right" | Statistics for each side, defined below. |
| "duration_seconds" | Elapsed seconds added by the CLI; not part of the Python compare() result. |

| Per-side field | Meaning |
|---|---|
| "path", "format", "matched_files" | Configured source pattern, effective format, and file count. |
| "rows_read" | All source records read, including malformed records. |
| "rows_skipped" | Malformed records skipped by policy plus skipped empty-key records. |
| "rows_invalid_keys" | Empty-key records skipped by policy; a subset of "rows_skipped". |
| "rows_matched" | Records whose key exists on the opposite side. |
| "rows_missing" | Records whose key is absent on the opposite side. Includes duplicate records. |
| "rows_written" | Missing records selected by the mode and written (or counted in a dry run). |
| "files" | Reader statistics per physical input file, plus invalid-key skip counts when applicable. |

Each side satisfies "rows_read = rows_skipped + rows_matched + rows_missing". Both sides are analyzed even in a one-direction mode, but only the selected side is written. The total "rows_written" is the sum of the two side-specific written counters.

For the bundled example, each side reads three rows, matches two, and is missing one on the opposite side. Total "rows_written" is two.

Per-file entries contain "path", "format", and nonzero read/skip counters. CSV entries also include "delimiter" and "header_used". "rows_invalid_keys" is included when nonzero. Matched, missing, and written counters are reported at side level, not file level.

"validate" detects comparison configurations automatically. Its result contains "valid", "operation", "output_columns" (including generated columns), "mode", "keys", and "input_patterns". Each input-pattern entry has "side", "path", "format", and "matched_files". Validation does not scan records; use a dry run to verify data and keys.

Handled configuration, record, I/O, encoding, CSV, and SQLite errors exit with code 2 and print an error to stderr. Check the exit code even if stdout contains JSON: a report-file write can fail after data output and stdout are complete.

## Storage and performance

CSV and JSONL inputs are streamed through the same readers used by merge jobs. JSON and STIX are loaded as complete documents, as in existing merge jobs.

Comparison reads each source once and stores keys in a temporary SQLite database. It also stores normalized records for the sides selected for output; dry runs store keys only. Indexed membership checks avoid an in-memory collection of all rows, and preserve the normalized records read during the initial pass. Temporary storage is removed after success or failure.

Provide enough free space in the operating system's temporary directory for the selected normalized datasets, both key sets, and their indexes, in addition to space for output. Dry runs still require disk space for keys and indexes.

The Python API exposes compare(config, dry_run=False, info=None) and validate_comparison(config). Use load_config(path) first to resolve paths relative to the configuration file.

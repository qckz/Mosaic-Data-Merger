# CSV Manipulator

`csv_merge.py` transforms CSV, JSON, JSONL, and STIX 2.1 files into CSV, JSON, or STIX output. CSV and JSONL are processed row-by-row; regular JSON and STIX Bundles are read as complete JSON documents. It needs only Python 3.10+ and the standard library.

A job may have one input (a transformation) or many inputs (a merge). Its output format is selected per job: CSV, JSON, or STIX 2.1.

## Run a job

```bash
python3 csv_merge.py validate --config example-job.json
python3 csv_merge.py run --config example-job.json --report ./output/report.json
python3 csv_merge.py run --config example-job.json --verbose
```

Use a dry run to execute mappings, transformations, filtering, validation, and deduplication without creating the output or rejects files:

```bash
python3 csv_merge.py run --config example-job.json --dry-run
```

Inspect an unfamiliar source before writing its configuration:

```bash
python3 csv_merge.py inspect ./input/source.csv
python3 csv_merge.py inspect ./input/source.csv --encoding windows-1252 --delimiter ';'
python3 csv_merge.py inspect ./input/events.jsonl
python3 csv_merge.py inspect ./input/threat-intelligence.json --format stix
```

The commands print JSON, which makes their output easy to store in automation logs.

### Diagnostics and progress messages

`validate` reports the format and number of files matched by every input path or wildcard pattern. `run` includes the same `input_patterns` summary plus separate statistics for each processed file.

Use `--verbose` to print progress information to standard error without changing the JSON report on standard output. For CSV inputs, this includes the actual delimiter and header mode used for each file. This is useful for detecting an incorrectly configured delimiter in automated jobs.

When a mapped header name cannot be found, the error now reports the delimiter used and the parsed headers. If the complete header was parsed as a single field and contains another common separator, it explicitly suggests checking the delimiter setting.

## Job configuration

Start by copying [example-job.json](example-job.json), then update its input and output paths for your job. For a complete STIX input-to-STIX Bundle output example, use [example-stix-job.json](example-stix-job.json). Relative paths are resolved from the configuration file's directory (not the current terminal directory). The top-level fields are:

- `output`: destination, format, schema, delimiter/encoding, output mode, and provenance columns.
- `inputs`: one specification per CSV, JSON, JSONL, or STIX source file, including its layout and field mapping.
- `transformations`: cleanup steps applied to normalized output fields.
- `filters`: conditions a row must satisfy to be included.
- `validation`: data-quality rules and how invalid records are handled.
- `deduplication`: optional bounded-memory duplicate detection.

### Output options

`output` defines the target file and format and is required.

| Option | Required | Meaning |
|---|---:|---|
| `path` | Yes | Output filename and location. Its parent directories are created when needed. |
| `format` | No | `csv` (default), `json` (a JSON array of output records), or `stix` (a STIX 2.1 Bundle). |
| `columns` | Yes | Ordered output header names. Every mapping target must appear here. |
| `delimiter` | No | One-character CSV delimiter; defaults to `,`. It also controls CSV rejects output. |
| `quote_all` | No | CSV only. When `true`, enclose every header and field in double quotes; defaults to `false`. Embedded `"` characters become `""`. |
| `encoding` | No | Output text encoding; defaults to `utf-8`. |
| `mode` | No | `replace` (default) creates a new file; `append` adds rows to a compatible existing file. |
| `atomic_write` | No | With `replace` (the default), write a temporary file and replace the destination only after success. |
| `add_provenance` | No | Adds `source_file` and `source_row` columns automatically. |

Appending checks that the existing CSV header is exactly the configured output schema. JSON and STIX output always use `replace`; `atomic_write` cannot be used with append mode.

### Input options

Each item in `inputs` requires `path` and `mapping`.

| Option | CSV | JSON / JSONL / STIX | Meaning |
|---|:---:|:---:|---|
| `path` | Yes | Yes | A single file path or a wildcard pattern such as `./input/list_*.csv`. |
| `format` | Optional | Optional | `csv`, `json`, `jsonl`, or `stix`. `.jsonl` defaults to JSONL, `.json` defaults to JSON, and other files default to CSV. |
| `encoding` | Optional | Optional | Source encoding; defaults to `utf-8`. |
| `mapping` | Yes | Yes | Source field-to-output-column mapping. |
| `on_malformed_row` | Optional | Optional | CSV: `error`, `skip`, or `pad`; JSON/JSONL/STIX: `error` or `skip` for invalid records. |
| `header` | Optional | No | `true` (default), `false`, or `auto`; applies only to CSV. |
| `delimiter` | Optional | No | One-character input separator. If omitted, the tool attempts CSV dialect detection. |
| `quotechar`, `escapechar` | Optional | No | One-character CSV quote/escape settings. |
| `doublequote`, `skipinitialspace`, `strict` | Optional | No | CSV parser behavior flags. |

### Wildcard input paths

`path` may contain standard filename wildcards: `*` (any characters), `?` (one character), character sets such as `[0-9]`, and recursive `**` directory patterns. All matching regular files use the same input settings, so they should share the configured format and layout.

```json
{
  "path": "./input/list_*.csv",
  "format": "csv",
  "header": false,
  "delimiter": ",",
  "mapping": {
    "1": "Place",
    "2": "Name"
  }
}
```

Matches are processed in lexically sorted path order (`list_01.csv`, then `list_02.csv`, and so on), one file at a time. A pattern that matches no regular files stops the job with a configuration error rather than silently producing incomplete output. The report retains separate statistics for every expanded file; enable `add_provenance` to retain each source filename and row number in the output.

### Mapping fields

`output.columns` fixes the exact column names and order. Every input `mapping` maps a source field to one of those names.

For a headerless source, use one-based positions:

```json
{
  "header": false,
  "mapping": { "1": "Place", "3": "Name" }
}
```

For a source with headers, source names are supported alongside positions:

```json
{
  "header": true,
  "mapping": { "City": "Place", "Customer name": "Name", "5": "Date" }
}
```

The same output field may be supplied by different inputs. For example, column `1` from a headerless CSV and key `city` from a JSONL file can both map to `Place`.

A field may be mapped to the same output field in different input files. Missing fields in a particular source are emitted empty, and transformations such as `set_default` can fill them. Within a single source, mapping two source fields to one output field is rejected to prevent accidental overwrites. Any or all source fields can be mapped; unmapped source fields are ignored.

### Fixed input values

Within an input's `mapping`, use the reserved `$constants` object to provide a hard-coded value for output columns that do not exist in that source file. Constants are applied before transformations, filters, and validation, so they behave like any other mapped value.

```json
"mapping": {
  "City": "Place",
  "Customer name": "Name",
  "$constants": {
    "Source": "monthly-customer-export",
    "Priority": 10
  }
}
```

`Source` and `Priority` must be declared in `output.columns`. A source field and a constant cannot both fill the same output column within one input specification. Constant strings, numbers, booleans, `null`, arrays, and objects are converted using the same rules as JSON values.

### Fully quoted CSV output

Set `quote_all` in `output` when a receiving system expects every CSV field to be double-quoted:

```json
"output": {
  "path": "./output/customers.csv",
  "format": "csv",
  "columns": ["Place", "Name"],
  "quote_all": true
}
```

This produces values such as `"Alice ""The Ace"""`; embedded double quotes are escaped according to the CSV standard. CSV headers are quoted too. `quote_all` is available only for CSV output.

Set `header` to `"auto"` only for exploratory or variable sources; explicit `true` or `false` is preferable in production. When `delimiter` is omitted, the tool tries to detect one, but explicit delimiters avoid ambiguity and are faster.

### JSONL inputs

JSONL contains one JSON object per line. Set `"format": "jsonl"`, or omit it for a file whose path ends in `.jsonl`. JSON object keys are the source field names, so mapping is always by key name:

```json
{
  "path": "./input/people.jsonl",
  "format": "jsonl",
  "mapping": {
    "city": "Place",
    "full_name": "Name",
    "created_at": "Date"
  },
  "on_malformed_row": "skip"
}
```

`header` and `delimiter` do not apply to JSONL. JSON `null` becomes an empty CSV value, booleans become `true` or `false`, and nested arrays/objects are stored as compact JSON within one CSV field. A malformed JSON line can stop the job (`error`, the default) or be skipped.

### JSON input and output

Set `"format": "json"` on an input to read either one JSON object or an array of JSON objects. Source object keys are mapped in exactly the same way as JSONL keys. JSON input is loaded as a complete document, so use JSONL for very large record streams.

Set `"format": "json"` in `output` to write a JSON array. The configured `output.columns` become the keys in every emitted object:

```json
"output": {
  "path": "./output/customers.json",
  "format": "json",
  "columns": ["Place", "Name", "Date"],
  "atomic_write": true
}
```

### STIX 2.1 input and output

STIX is JSON-based threat-intelligence data. A `stix` input accepts either a STIX Bundle or one STIX object. For a Bundle, each object in `objects` is processed as one source record. Top-level STIX properties such as `type`, `id`, `name`, `pattern`, and `created` can be mapped to output columns; nested values are preserved as compact JSON strings.

```json
{
  "path": "./input/intelligence.json",
  "format": "stix",
  "mapping": {
    "type": "Object type",
    "id": "STIX ID",
    "name": "Name",
    "pattern": "Pattern"
  }
}
```

STIX output writes one configured STIX object for each normalized row and wraps them in a STIX 2.1 Bundle. `properties` maps output columns to properties on the generated STIX object; `static` supplies constant JSON-valued properties shared by every object.

[example-stix-job.json](example-stix-job.json) is a complete STIX example: it reads `indicator` objects from a Bundle, maps `name`, `pattern`, and `id`, then emits one STIX `note` per accepted row. The configured `object_refs` value should be replaced with IDs relevant to your own feed.

```json
"output": {
  "path": "./output/notes.json",
  "format": "stix",
  "columns": ["Title", "Content"],
  "stix": {
    "object_type": "note",
    "properties": {
      "Title": "abstract",
      "Content": "content"
    },
    "static": {
      "object_refs": ["indicator--00000000-0000-4000-8000-000000000001"]
    }
  }
}
```

The tool generates Bundle and object IDs and STIX 2.1 timestamps by default; set `bundle_id`, `timestamp`, `add_timestamps`, or `static` inside `output.stix` when your integration needs specific values. It checks the Bundle/object envelope, but does not perform full object-type schema validation—configure all properties required by your chosen STIX object type. `note` is the default object type.

### Transformations

Transformations run in the configured order for each output field. Available types:

- `trim`, `lowercase`, `uppercase`, `title_case`
- `set_default` (`value` is used only when the field is empty)
- `replace` (`old`, optional `new`)
- `null_if` (`values` array)
- `date_format` (`input_formats` array and `output_format`)

For example, this replaces a missing place, removes surrounding whitespace, and standardizes a date:

```json
"transformations": {
  "Place": [
    { "type": "trim" },
    { "type": "set_default", "value": "Unknown" }
  ],
  "Date": [
    {
      "type": "date_format",
      "input_formats": ["%d-%m-%Y", "%Y/%m/%d"],
      "output_format": "%Y-%m-%d"
    }
  ]
}
```

### Filters and validation

All filters must match for a row to be written. Operators are `not_empty`, `empty`, `equals`, `not_equals`, `contains`, `in` (with `values`), and `regex`.

Examples:

```json
"filters": [
  { "column": "Name", "operator": "not_empty" },
  { "column": "Place", "operator": "in", "values": ["Amsterdam", "Utrecht"] }
]
```

Validation rules support `required`, `date` (with `format`), `number`, and `regex` (with `pattern`). Set `validation.on_error` to:

- `error` — stop immediately (the default).
- `reject` — write the normalized row and `_error` message to `rejects_path`.
- `skip` — omit the row and record it in the report.

For CSV, `on_malformed_row` controls rows that are too short for the requested mapped positions: `error` (default), `skip`, or `pad` (use empty values for absent fields). For JSONL, JSON, and STIX, it controls invalid records: `error` or `skip`. A syntactically invalid JSON/STIX document always stops the job because its records cannot be read safely.

### Excluding rows with a CSV list

Use top-level `exclusions` to remove normalized rows that appear in one or more CSV exclusion lists. Exclusions run after transformations and ordinary filters, but before validation, deduplication, and writing the CSV, JSON, or STIX output.

```json
"exclusions": [
  {
    "path": "./input/excluded-customers.csv",
    "format": "csv",
    "header": false,
    "delimiter": ",",
    "mapping": {
      "1": "Email",
      "2": "Country"
    },
    "keys": ["Email", "Country"]
  }
]
```

The exclusion list may have headers (`"Email address": "Email"`) or use one-based positions when `header` is `false`. `keys` defines the composite match: **all** listed output columns must match one exclusion-list row for the row to be removed. A one-key list excludes all rows with that one value; multiple keys use AND matching, not OR matching.

Exclusion paths support the same wildcards as normal inputs. Multiple exclusion lists are allowed; a row is removed when it matches any complete key tuple in any list. By default, entries or source rows with an empty key part do not match; set `"allow_empty_keys": true` only when blank values should be eligible for exclusion.

The list is stored in a temporary SQLite index, so large exclusion lists do not require keeping all keys in memory. Run reports include `rows_excluded` plus per-list matched-file, key-loaded, duplicate-key, skipped-row, and match counts.

### Deduplication and reporting

To retain only the first row for each combination of output values, enable deduplication:

```json
"deduplication": {
  "enabled": true,
  "keys": ["Place", "Name"],
  "keep": "first"
}
```

Duplicate tracking uses temporary disk-backed SQLite storage rather than retaining all source rows in memory. Each run prints a JSON report with per-file and total read, written, filtered, rejected, skipped, and deduplicated row counts. Add `--report ./output/report.json` to store the same report in a file.

### Large files and safety

Normal merging, transformations, filters, and validation use constant memory. Deduplication uses a temporary SQLite key store so it also remains bounded-memory; it supports `keep: "first"` only. Output uses atomic replacement by default: downstream systems see either the old complete file or the new complete file, never a partially written result. Atomic replacement cannot be combined with append mode.

When `add_provenance` is true, `source_file` and `source_row` are appended to the output schema automatically. This is especially useful for rejected-row investigations.

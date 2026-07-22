# CSV Manipulator

`csv_merge.py` transforms CSV and JSONL files into one standardized CSV. It streams records rather than loading all input rows into memory, making it suitable for very large files. It needs only Python 3.10+ and the standard library.

A job may have one input (a transformation) or many inputs (a merge). The output is always CSV.

## Run a job

```bash
python3 csv_merge.py validate --config example-job.json
python3 csv_merge.py run --config example-job.json --report ./output/report.json
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
```

The commands print JSON, which makes their output easy to store in automation logs.

## Job configuration

Start by copying [example-job.json](example-job.json), then update its input and output paths for your job. Relative paths are resolved from the configuration file's directory (not the current terminal directory). The top-level fields are:

- `output`: destination, output schema, delimiter/encoding, output mode, and provenance columns.
- `inputs`: one specification per CSV or JSONL source file, including its layout and field mapping.
- `transformations`: cleanup steps applied to normalized output fields.
- `filters`: conditions a row must satisfy to be included.
- `validation`: data-quality rules and how invalid records are handled.
- `deduplication`: optional bounded-memory duplicate detection.

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

A field may be mapped to the same output field in different input files. Missing fields in a particular source are emitted empty, and transformations such as `set_default` can fill them. Within a single source, mapping two source fields to one output field is rejected to prevent accidental overwrites. Any or all source fields can be mapped; unmapped source fields are ignored.

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

### Transformations

Transformations run in the configured order for each output field. Available types:

- `trim`, `lowercase`, `uppercase`, `title_case`
- `set_default` (`value` is used only when the field is empty)
- `replace` (`old`, optional `new`)
- `null_if` (`values` array)
- `date_format` (`input_formats` array and `output_format`)

### Filters and validation

All filters must match for a row to be written. Operators are `not_empty`, `empty`, `equals`, `not_equals`, `contains`, `in` (with `values`), and `regex`.

Validation rules support `required`, `date` (with `format`), `number`, and `regex` (with `pattern`). Set `validation.on_error` to:

- `error` — stop immediately (the default).
- `reject` — write the normalized row and `_error` message to `rejects_path`.
- `skip` — omit the row and record it in the report.

For CSV, `on_malformed_row` controls rows that are too short for the requested mapped positions: `error` (default), `skip`, or `pad` (use empty values for absent fields). For JSONL, it controls malformed JSON records: `error` or `skip`.

### Large files and safety

Normal merging, transformations, filters, and validation use constant memory. Deduplication uses a temporary SQLite key store so it also remains bounded-memory; it supports `keep: "first"` only. Output uses atomic replacement by default: downstream systems see either the old complete file or the new complete file, never a partially written result. Atomic replacement cannot be combined with append mode.

When `add_provenance` is true, `source_file` and `source_row` are appended to the output schema automatically. This is especially useful for rejected-row investigations.

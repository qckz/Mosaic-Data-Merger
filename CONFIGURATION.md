# Configuration reference

This is the complete job-configuration reference for Mosaic Data Merger. A job is one JSON object that reads one or more input files, normalizes their records to one output schema, and writes one result.

Paths in a job are resolved relative to the configuration file, not the terminal's current directory. The examples below assume this layout:

```text
project/
├── config/
│   └── job.json
├── input/
└── output/
```

Therefore a job stored at `config/job.json` normally uses `../input/...` and `../output/...` paths.

## Contents

- [Quick start](#quick-start)
- [Configuration anatomy](#configuration-anatomy)
- [Output reference](#output-reference)
- [Input reference](#input-reference)
- [Mappings and defaults](#mappings-and-defaults)
- [Transformations](#transformations)
- [Filters](#filters)
- [Validation and rejects](#validation-and-rejects)
- [Exclusions](#exclusions)
- [Deduplication](#deduplication)
- [Format behavior matrix](#format-behavior-matrix)
- [Processing order and reports](#processing-order-and-reports)
- [Recipes](#recipes)
- [Limits and common errors](#limits-and-common-errors)

## Quick start

This is the smallest practical CSV-to-CSV job:

```json
{
  "output": {
    "path": "../output/people.csv",
    "columns": ["Place", "Name"]
  },
  "inputs": [
    {
      "path": "../input/people.csv",
      "mapping": {
        "City": "Place",
        "Full name": "Name"
      }
    }
  ]
}
```

Run it with:

```bash
mosaic-data-merger validate --config config/job.json
mosaic-data-merger run --config config/job.json
```

`output` and `inputs` are required. Every other top-level section is optional.

## Configuration anatomy

| Section | Required | Type | Purpose |
|---|:---:|---|---|
| `output` | Yes | object | Destination, output format, and ordered output schema. |
| `inputs` | Yes | array | One or more source-file specifications. |
| `transformations` | No | object | Ordered value changes after mapping. |
| `filters` | No | array | Conditions a normalized row must all satisfy. |
| `validation` | No | object | Data-quality rules and invalid-row policy. |
| `exclusions` | No | array | CSV lists whose matching rows are removed. |
| `deduplication` | No | object | Disk-backed duplicate removal. |

The output schema is the centre of the job. Input mappings, transformations, filters, validations, exclusions, and deduplication all refer to names in `output.columns`.

## Output reference

`output` is required.

| Option | Type | Default | Valid values / format | Meaning |
|---|---|---|---|---|
| `path` | string | — | Relative or absolute file path | Required destination file. Parent directories are created when writing. |
| `format` | string | `"csv"` | `csv`, `json`, `jsonl`, `stix` | Output representation. |
| `columns` | array of strings | — | Non-empty, unique names | Required ordered output schema. CSV headers and JSON object keys use this order. |
| `encoding` | string | `"utf-8"` | Python text encoding name | Encoding for output and CSV rejects. |
| `mode` | string | `"replace"` | `replace`, `append` | Whether to replace or add to an existing output. |
| `atomic_write` | boolean | `true` for replace | `true`, `false` | Write a temporary file and replace the destination only after success. |
| `add_provenance` | boolean | `false` | `true`, `false` | Automatically appends `source_file` and `source_row` to the schema. |
| `delimiter` | one-character string | `,` | For example `,`, `;`, `\\t` | CSV separator; also used by CSV rejects. |
| `quotechar` | one-character string or `null` | Python CSV default | Usually `"` | CSV quote character. |
| `escapechar` | one-character string or `null` | Python CSV default | Usually omitted | CSV escape character. |
| `doublequote` | boolean | Python CSV default | `true`, `false` | Whether an embedded quote is escaped by doubling it. |
| `quote_all` | boolean | `false` | `true`, `false` | CSV only. Quotes every header and field with double quotes. |
| `stix` | object | — | See [STIX output](#stix-output) | Required only for `format: "stix"`. |

### Output modes

| Format | Replace | Append | Notes |
|---|:---:|:---:|---|
| CSV | Yes | Yes | Append verifies that the existing header exactly matches `columns`. |
| JSON | Yes | No | Writes one JSON array. |
| JSONL | Yes | Yes | Appends complete JSON objects, one per line. |
| STIX | Yes | No | Writes one STIX Bundle. |

`atomic_write` is available only with `mode: "replace"`. It prevents consumers from seeing a partly written replacement file.

### CSV output

```json
"output": {
  "path": "../output/people.csv",
  "format": "csv",
  "delimiter": ";",
  "columns": ["Place", "Name"],
  "quote_all": true
}
```

`quote_all: true` produces fully quoted CSV and doubles embedded double quotes according to the CSV standard. It requires the double-quote quote character and `doublequote: true`.

### JSON and JSONL output

```json
"output": {
  "path": "../output/people.jsonl",
  "format": "jsonl",
  "mode": "append",
  "columns": ["Place", "Name"]
}
```

JSON output is a single array:

```json
[
  {"Place":"Amsterdam","Name":"Alice"},
  {"Place":"Utrecht","Name":"Bob"}
]
```

JSONL output contains the same objects one per line, which makes it suitable for incremental appends and large outputs.

### STIX output

STIX output creates one configured STIX 2.1 object per normalized row and wraps all objects in a Bundle.

```json
"output": {
  "path": "../output/notes.json",
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
    },
    "bundle_id": "bundle--00000000-0000-4000-8000-000000000002",
    "timestamp": "2026-07-24T12:00:00.000Z",
    "add_timestamps": true
  }
}
```

| `output.stix` option | Type | Default | Meaning |
|---|---|---|---|
| `object_type` | string | `"note"` | Type of each generated STIX object. |
| `properties` | object | `{}` | Maps output columns to STIX property names. Empty output values are omitted. |
| `static` | object | `{}` | JSON-compatible properties copied to every generated object. |
| `bundle_id` | string | generated | STIX Bundle ID. |
| `timestamp` | string | current UTC timestamp | Timestamp used for generated `created` and `modified` values. |
| `add_timestamps` | boolean | `true` | Add generated `created` and `modified` values unless already present in `static`. |

The tool validates the Bundle and object envelope, but it does not fully validate each STIX object type. Supply the properties required by the chosen STIX type.

## Input reference

`inputs` must be a non-empty array. Each item describes one file or wildcard pattern. One job may mix all supported input formats.

| Option | Type | Default | Applies to | Meaning |
|---|---|---|---|---|
| `path` | string | — | All | Required file path or wildcard pattern. |
| `format` | string | inferred | All | `csv`, `json`, `jsonl`, or `stix`. |
| `encoding` | string | `"utf-8"` | All | Source text encoding. |
| `mapping` | object | — | All | Required source-field-to-output mapping. |
| `on_malformed_row` | string | `"error"` | All | Invalid-record policy; allowed values depend on format. |
| `header` | boolean or string | `true` | CSV only | Whether the first row holds CSV headers. |
| `delimiter` | one-character string | detected | CSV only | Input CSV separator. |
| `quotechar` | one-character string or `null` | Python CSV default | CSV only | CSV quote character. |
| `escapechar` | one-character string or `null` | Python CSV default | CSV only | CSV escape character. |
| `doublequote` | boolean | Python CSV default | CSV only | Double embedded quote characters. |
| `skipinitialspace` | boolean | Python CSV default | CSV only | Ignore spaces immediately following a delimiter. |
| `strict` | boolean | Python CSV default | CSV only | Raise CSV parser errors for malformed quoting. |

### Input format inference

If `format` is omitted:

| Filename suffix | Inferred format |
|---|---|
| `.jsonl` | `jsonl` |
| `.json` | `json` |
| Anything else | `csv` |

Set `format: "stix"` explicitly for STIX input, including files with a `.json` suffix.

### CSV headers and delimiters

| `header` value | Meaning |
|---|---|
| `true` | First row is a header. This is the default. |
| `false` | No header; map source columns with one-based positions such as `"1"`. |
| `"auto"` | Use Python's CSV header heuristic. Useful for exploration, but explicit `true` or `false` is safer in production. |

If `delimiter` is omitted, the tool samples the file and tries `,`, `;`, tab, `|`, and `:`; it falls back to a comma. Specify the delimiter for predictable production jobs.

```json
{
  "path": "../input/locations.csv",
  "format": "csv",
  "header": false,
  "delimiter": ";",
  "mapping": {
    "1": "Place",
    "3": "Name"
  }
}
```

CSV positions are one-based. `"1"` means the first source column, not the second.

### Wildcard paths

`path` supports standard filename patterns for every supported input format:

| Pattern | Meaning |
|---|---|
| `*` | Any number of characters. |
| `?` | One character. |
| `[0-9]` | One character from a set or range. |
| `**` | Recursive directories. |

```json
{
  "path": "../input/202607205435435-job_*.json",
  "format": "json",
  "mapping": {
    "city": "Place",
    "name": "Name"
  }
}
```

Matching files are processed one at a time in lexical path order. A pattern with no matches stops the job with a configuration error. All matching files must use the same configured format and layout.

Use wildcards with `validate` or `run`. The `inspect` command accepts one physical file only.

### Malformed-record policy

| Format | Allowed values | Meaning |
|---|---|---|
| CSV | `error`, `skip`, `pad` | A row too short for a required positional mapping stops, skips, or supplies empty missing values. |
| JSONL | `error`, `skip` | Invalid lines or non-object records stop or skip. |
| JSON | `error`, `skip` | Non-object records in an otherwise readable document stop or skip. |
| STIX | `error`, `skip` | Invalid individual STIX objects stop or skip. |

A syntactically invalid JSON or STIX document always stops the job because its records cannot be read safely.

## Mappings and defaults

Each input `mapping` maps source fields to names in `output.columns`. Unmapped source data is ignored.

### Required source mapping

Use a string when the source field must exist:

```json
"mapping": {
  "City": "Place",
  "Customer name": "Name"
}
```

For a headered CSV, a missing required header is an error. This is intentional: it catches misspelled headers and unexpected source layouts.

### Default when a source field is absent

Use the object form when a source field may be absent:

```json
"mapping": {
  "City": "Place",
  "Country": {
    "output_column": "Country",
    "default_if_missing": "Unknown"
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `output_column` | non-empty string | Required destination name; it must appear in `output.columns`. |
| `default_if_missing` | any JSON value | Used only when the source CSV header or JSON, JSONL, or STIX key does not exist. |

If a CSV has the `Country` header, its value is used. If the header is absent, `Unknown` is used. If the header exists but a row's value is blank, the blank value is preserved. Use [`default_if_empty`](#transformations) as well when blanks need a fallback.

The object must contain exactly `output_column` and `default_if_missing`. This keeps the syntax explicit and prevents silent misspellings.

### Fixed values: `$constants`

`$constants` assigns a fixed output value for every record from that input:

```json
"mapping": {
  "City": "Place",
  "$constants": {
    "Source": "monthly-customer-export",
    "Priority": 10
  }
}
```

`$constants` is different from `default_if_missing`:

| Feature | When it applies | Can a source value overwrite it? |
|---|---|:---:|
| `default_if_missing` | Only when the source field/key is absent | Yes, when the field/key exists |
| `default_if_empty` | Only when a mapped output value is empty | Not applicable; it is a transformation |
| `$constants` | Every record from that input | No |

Within one input, an output column may be filled by only one mapping rule or constant. The tool rejects duplicate targets to prevent accidental overwrites.

### JSON value conversion

Source JSON and STIX values become output text values as follows:

| Source value | Output value |
|---|---|
| string | unchanged |
| number | decimal text |
| `true` / `false` | `"true"` / `"false"` |
| `null` | empty string |
| array or object | compact JSON text in one output field |

Mappings address top-level JSON/STIX keys. Nested-path expressions such as `customer.address.country` are not currently supported.

## Transformations

`transformations` is an object keyed by output column. Actions run in the listed order after mapping and before filters, exclusions, validation, and deduplication.

```json
"transformations": {
  "Place": [
    { "type": "trim" },
    { "type": "title_case" }
  ],
  "Country": [
    { "type": "default_if_empty", "value": "Unknown" }
  ]
}
```

| `type` | Required extra fields | Meaning |
|---|---|---|
| `trim` | — | Remove leading and trailing whitespace. |
| `lowercase` | — | Convert to lowercase. |
| `uppercase` | — | Convert to uppercase. |
| `title_case` | — | Apply Python title case. |
| `default_if_empty` | `value` | Use `value` only when the current output value is empty. |
| `replace` | `old`; optional `new` | Replace every occurrence of `old` with `new` (empty by default). |
| `null_if` | `values` array | Set the value to empty when it exactly matches any listed value. |
| `date_format` | `input_formats` array and `output_format` | Parse with the first matching input format and reformat it. |

`set_default` is not supported. Use `default_if_empty`.

## Filters

`filters` is an array. Every condition must match for a row to continue; this is logical AND.

```json
"filters": [
  { "column": "Name", "operator": "not_empty" },
  { "column": "Country", "operator": "in", "values": ["NL", "BE"] }
]
```

| `operator` | Required field | Meaning |
|---|---|---|
| `not_empty` | — | Value is not empty. |
| `empty` | — | Value is empty. |
| `equals` | `value` | Exact text equality. |
| `not_equals` | `value` | Exact text inequality. |
| `contains` | `value` | Value contains the supplied text. |
| `in` | `values` array | Value equals any listed value. |
| `regex` | `value` | Value matches the regular expression. |

## Validation and rejects

```json
"validation": {
  "on_error": "reject",
  "rejects_path": "../output/rejected.csv",
  "rules": [
    { "column": "Place", "type": "required" },
    { "column": "Date", "type": "date", "format": "%Y-%m-%d" },
    { "column": "Amount", "type": "number" },
    { "column": "Email", "type": "regex", "pattern": ".+@.+" }
  ]
}
```

| `validation` option | Type | Default | Meaning |
|---|---|---|---|
| `on_error` | string | `"error"` | `error` stops, `reject` writes a rejects row, `skip` omits the invalid row. |
| `rejects_path` | string | — | Required when `on_error` is `reject`. |
| `rules` | array | `[]` | Validation rules. |

| Rule `type` | Required fields | Meaning |
|---|---|---|
| `required` | `column` | Value must not be empty. |
| `date` | `column`, `format` | Value must parse with the supplied Python datetime format. |
| `number` | `column` | Value must parse as a floating-point number. |
| `regex` | `column`, `pattern` | Value must match the regular expression. |

Rejects contain the normalized output fields plus an `_error` column. Validation is performed after transformations, filters, and exclusions.

## Exclusions

`exclusions` removes normalized rows that match records in one or more CSV exclusion lists. Exclusion checks run after transformations and filters, but before validation, deduplication, and output.

```json
"exclusions": [
  {
    "path": "../input/excluded-customers.csv",
    "header": false,
    "delimiter": ",",
    "mapping": {
      "1": "Email",
      "2": "Country"
    },
    "keys": ["Email", "Country"],
    "on_malformed_row": "skip",
    "allow_empty_keys": false
  }
]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `path` | string | — | Required CSV file or wildcard pattern. |
| `format` | string | `"csv"` | Must currently be `csv`. |
| `encoding` | string | `"utf-8"` | Exclusion-list encoding. |
| `header` | boolean or `"auto"` | `true` | CSV header behavior. |
| `delimiter` and CSV options | — | detected | Same behavior as normal CSV inputs. |
| `mapping` | object | — | Required source-field-to-output-column mapping. Constants and optional defaults are not available here. |
| `keys` | array of strings | — | Required output columns used to match rows. Each key must be mapped. |
| `on_malformed_row` | string | `"error"` | `error`, `skip`, or `pad`. |
| `allow_empty_keys` | boolean | `false` | Allow an empty key part to participate in matching. |

For multiple keys, all key values must match one exclusion record: `keys` uses AND matching, not OR matching. Multiple exclusion lists are allowed; matching any complete key tuple removes the row.

Exclusion keys are held in a temporary SQLite index, so large lists do not require loading all keys into memory.

## Deduplication

```json
"deduplication": {
  "enabled": true,
  "keys": ["Place", "Name"],
  "keep": "first"
}
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable duplicate removal. |
| `keys` | array of output-column names | — | Required when enabled; rows with the same complete tuple are duplicates. |
| `keep` | string | `"first"` | Only `first` is currently supported. |

Duplicate keys are stored in a temporary SQLite database. The first matching row is written; later matches are counted as deduplicated and skipped.

## Format behavior matrix

| Capability | CSV | JSON | JSONL | STIX |
|---|:---:|:---:|:---:|:---:|
| Header handling | Yes | No | No | No |
| One-based position mapping | Yes | No | No | No |
| Key-name mapping | Yes, with header | Yes | Yes | Yes |
| Input streaming | Yes | No | Yes | No |
| Wildcard input paths | Yes | Yes | Yes | Yes |
| `pad` malformed-row policy | Yes | No | No | No |
| Append output | CSV output only | No | JSONL output only | No |

Regular JSON and STIX documents are read as complete JSON documents. Prefer JSONL for very large JSON record streams.

## Processing order and reports

For every source record, Mosaic Data Merger performs these steps:

1. Create the ordered output row and apply `$constants` and any `default_if_missing` values.
2. Map source values that exist.
3. Add provenance values when enabled.
4. Run transformations.
5. Apply all filters.
6. Check exclusions.
7. Apply validation rules.
8. Check deduplication.
9. Write the row.

`run` prints a JSON report. Important fields include:

| Report field | Meaning |
|---|---|
| `rows_read` | Source records examined. |
| `rows_written` | Output records written. |
| `rows_filtered` | Rows removed by filters. |
| `rows_excluded` | Rows removed by exclusion lists. |
| `rows_rejected` | Invalid rows written to rejects output. |
| `rows_skipped` | Malformed or invalid records skipped by policy. |
| `rows_deduplicated` | Rows removed as duplicates. |
| `files` | Per-expanded-file statistics and detected CSV layout. |
| `input_patterns` | Each configured input pattern and match count. |
| `exclusions` | Per-exclusion-list key and match statistics. |

Use `--verbose` to send per-file progress, CSV delimiter, and header information to standard error without changing the JSON report on standard output. Use `--report /path/to/report.json` to save the same report.

## Recipes

### Merge a headerless CSV and JSONL file

```json
{
  "output": {
    "path": "../output/people.csv",
    "columns": ["Place", "Name"]
  },
  "inputs": [
    {
      "path": "../input/legacy.csv",
      "header": false,
      "mapping": {"1": "Place", "2": "Name"}
    },
    {
      "path": "../input/new-people.jsonl",
      "format": "jsonl",
      "mapping": {"city": "Place", "full_name": "Name"}
    }
  ]
}
```

### Use a default for a missing CSV header and a blank value

```json
{
  "output": {
    "path": "../output/people.csv",
    "columns": ["Place", "Country"]
  },
  "inputs": [
    {
      "path": "../input/people-*.csv",
      "mapping": {
        "City": "Place",
        "Country": {
          "output_column": "Country",
          "default_if_missing": "Unknown"
        }
      }
    }
  ],
  "transformations": {
    "Country": [
      {"type": "default_if_empty", "value": "Unknown"}
    ]
  }
}
```

### Write an appendable JSONL feed

```json
{
  "output": {
    "path": "../output/events.jsonl",
    "format": "jsonl",
    "mode": "append",
    "columns": ["Place", "Event"]
  },
  "inputs": [
    {
      "path": "../input/events-*.jsonl",
      "format": "jsonl",
      "mapping": {"city": "Place", "event": "Event"}
    }
  ]
}
```

### Exclude rows on a composite key

```json
{
  "output": {
    "path": "../output/people.csv",
    "columns": ["Email", "Country"]
  },
  "inputs": [
    {
      "path": "../input/people.csv",
      "mapping": {"Email": "Email", "Country": "Country"}
    }
  ],
  "exclusions": [
    {
      "path": "../input/do-not-export.csv",
      "header": false,
      "mapping": {"1": "Email", "2": "Country"},
      "keys": ["Email", "Country"]
    }
  ]
}
```

### Produce STIX notes

Use [examples/example-stix-job.json](examples/example-stix-job.json) as a complete STIX input-to-output job. Replace the illustrative `object_refs` identifiers with IDs from your own feed.

## Limits and common errors

| Situation | Cause and solution |
|---|---|
| `Input pattern matched no files` | The path is relative to the configuration file. Correct the path or wildcard. |
| `Input file does not exist` | Use one physical file, or ensure the pattern contains a wildcard. |
| Missing CSV header error | Use the exact header name, correct `delimiter`, or use `default_if_missing` when absence is expected. |
| Header parsed as one field | The delimiter is probably wrong. Set `delimiter` explicitly. |
| Cannot append because header differs | Existing CSV headers must exactly match the configured output schema. |
| `set_default` unsupported | Rename it to `default_if_empty`. |
| JSON runs out of memory | Use JSONL for large record streams; regular JSON is read as one document. |
| Need a fixed value for every row | Use `$constants`, not `default_if_missing`. |

For every available option in one copyable catalog, see [examples/config-catalog.json](examples/config-catalog.json). For architecture and extension points, see [ARCHITECTURE.md](ARCHITECTURE.md).

For the exact JSON returned by inspect, validate, and run, including every report field and counter, see [CLI_OUTPUT.md](CLI_OUTPUT.md).

# Roadmap

This roadmap describes the intended direction of Mosaic Data Merger. It is not a release contract: priorities may change as real-world data sets and automation needs reveal the most valuable work.

## Version 0.6 — structured values and derived data

Version 0.6 is planned as a cohesive improvement to how Mosaic reads and reshapes structured data. The objective is to make JSON, JSONL, STIX, and delimited CSV fields equally practical without guessing at a source file's meaning.

### Nested JSON and STIX paths

Allow mappings to select values below the top-level object using an explicit path:

~~~json
{
  "mapping": {
    "user.address.city": "City",
    "network.ip": "IPAddress",
    "observables[0].value": "FirstObservable"
  }
}
~~~

This would work for JSON, JSONL, and STIX input. An absent path should behave predictably with the existing "default_if_missing" mapping option. Mosaic must distinguish a path that is absent from a path whose value is present but empty.

### Deliberate list and array handling

JSON and STIX can contain native arrays:

~~~json
{
  "name": "Suspicious login",
  "tags": ["authentication", "high-risk", "external"]
}
~~~

CSV has no array type, but a cell can represent several values with a known separator:

~~~csv
Name,Tags
Suspicious login,authentication|high-risk|external
~~~

Mosaic should never infer that a character such as "|" represents an array. A configuration must opt in to parsing it. The same list operations should then apply to native JSON arrays and explicitly parsed CSV pseudo-arrays.

The vocabulary is intentionally precise:

| Operation | Input | Result | Purpose |
|---|---|---|---|
| "split" | text | internal list | Parse a delimited CSV-style value. |
| "take" | list | one value | Choose the first, last, or a specified index. |
| "join" | list | text | Combine every item into one output cell. |
| "to_json" | list | JSON text | Preserve the list representation in a scalar output field. |
| "explode" | list | multiple rows | Produce one copy of the row for each list item. |

"split" does not copy rows. It changes a scalar value into an internal list. "explode" is the separate, explicit operation that multiplies rows.

For example:

~~~json
{
  "transformations": [
    {
      "type": "split",
      "column": "Tags",
      "separator": "|"
    },
    {
      "type": "explode",
      "column": "Tags"
    }
  ]
}
~~~

would produce this output:

~~~csv
Name,Tags
Suspicious login,authentication
Suspicious login,high-risk
Suspicious login,external
~~~

"join" combines every list value; it does not silently retain the first item. Selecting one value is explicit:

~~~json
{
  "type": "take",
  "column": "Tags",
  "position": "first"
}
~~~

The first version should support one-column list transformations. Exploding several columns together needs an explicit later design:

- "zip" pairs items by position, such as "IP[0]" with "Port[0]";
- "cartesian" produces every possible combination.

Neither behavior should be the implicit default because they can produce very different data volumes and meanings.

### Derived-column transformations

Add a small, composable set of transformations that solve common automation work without custom scripts:

- concatenate columns with a configured separator;
- split text and select a part;
- replace literal text;
- regular-expression extraction or replacement;
- convert case;
- parse and format dates;
- choose the first non-empty value from several columns.

For example:

~~~json
{
  "type": "concat",
  "columns": ["FirstName", "LastName"],
  "output_column": "FullName",
  "separator": " "
}
~~~

All transformations should remain explicit about their input column, output column, handling of missing values, and error behavior.

### Operational principles

The 0.6 work should preserve Mosaic's current operational principles:

- stream CSV and JSONL records where possible;
- retain schema-driven, configuration-only jobs;
- make data-loss or row-multiplication operations explicit;
- keep output reports accurate when transformations remove, reject, skip, or expand records;
- document each configuration option alongside an example.

## Future ideas

These are useful directions, but are intentionally not committed to the next release:

- lookup/join enrichment from a second input file;
- richer reject output formats and error detail;
- report metrics such as null counts and validation failures by rule;
- reusable schema profiles and shared configuration fragments;
- aggregation and group-by operations;
- checkpointing for resumable very-large jobs;
- additional format adapters such as XML, Parquet, or spreadsheet input.

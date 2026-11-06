# Sample data

These small files support the paths used by `../config-catalog.json`.
They demonstrate CSV with and without headers, wildcard inputs, CSV exclusions,
JSON, JSONL, and a STIX 2.1 Bundle. They are deliberately tiny and safe to use
for local validation and experimentation.

The CSV assets and JSONL inventory files prefixed with `compare-` support
`../example-compare-job.json`. ID 1 is only in the CSV, ID 4 is only in the
JSONL, and IDs 2 and 3 occur in both. ID 2 deliberately has a different name:
comparison by ID checks presence, not changes to other fields.

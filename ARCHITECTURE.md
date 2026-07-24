# Architecture

Mosaic Data Merger is a standard-library package with a streaming pipeline. The public command is implemented in `mosaic_data_merger.cli`; the same operations can be imported from `mosaic_data_merger` in Python automation.

| Module | Responsibility |
|---|---|
| `config` | Load and validate the JSON job schema. |
| `paths` | Resolve config-relative paths and expand wildcards. |
| `csvio` | CSV dialect detection and parser/writer options. |
| `mapping` | Source-format detection and normalization into output fields. |
| `processing` | Transformations, filters, and validation rules. |
| `stores` | Temporary SQLite stores for deduplication and exclusions. |
| `writers` | Streaming CSV/JSON/JSONL/STIX output and atomic destinations. |
| `pipeline` | Source iteration, row orchestration, reporting, and inspection. |

The pipeline processes CSV and JSONL one record at a time. JSON and STIX documents remain whole-document inputs by design; output writers remain streaming. Deduplication and exclusions use temporary SQLite databases rather than materializing input rows in memory.

To add a format, implement its reader branch in `pipeline`, reuse `mapping.row_with_constants` and `processing` for normalization, then add its validation and documented configuration option. To add an output format, add a writer with `writerow` and `close`, register it in the pipeline, and extend configuration validation.

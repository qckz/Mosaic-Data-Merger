# Architecture

Mosaic Data Merger is a standard-library package with a streaming pipeline. The public command is implemented in `mosaic_data_merger.cli`; the same operations can be imported from `mosaic_data_merger` in Python automation.

| Module | Responsibility |
|---|---|
| `config` | Load and validate the JSON job schema. |
| `paths` | Resolve config-relative paths and expand wildcards. |
| `csvio` | CSV dialect detection and parser/writer options. |
| `mapping` | Source-format detection and normalization into output fields. |
| `readers` | Shared CSV/JSON/JSONL/STIX parsing and normalized row iteration. |
| `comparison` | Comparison configuration, indexed membership checks, and per-side reporting. |
| `processing` | Transformations, filters, and validation rules. |
| `stores` | Temporary SQLite stores for deduplication and exclusions. |
| `writers` | Streaming CSV/JSON/JSONL/STIX output and atomic destinations. |
| `pipeline` | Merge row orchestration, reporting, and inspection. |

The pipeline processes CSV and JSONL one record at a time. JSON and STIX documents remain whole-document inputs by design; output writers remain streaming. Deduplication and exclusions use temporary SQLite databases rather than materializing input rows in memory.

Comparison uses the shared readers to load mapped records and composite keys into a temporary SQLite database. It indexes membership by side and key, then writes unmatched rows in source order. This preserves duplicate rows without retaining datasets in memory. The database is closed and removed on success or failure.

To add a format, implement its reader branch in `readers`, reuse `mapping.row_with_constants` for normalization, then add its validation and inspection support. To add an output format, add a writer with `writerow` and `close`, register it in both the merge pipeline and `writers.open_output`, and extend configuration validation.

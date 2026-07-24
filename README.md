# Mosaic Data Merger

An offline, schema-driven data transformation, merge, and exclusion tool.

Mosaic Data Merger transforms one or many CSV, JSON, JSONL, and STIX 2.1 files into CSV, JSON, JSONL, or STIX output. It is designed for automation and large files: CSV and JSONL are streamed row by row, while deduplication and exclusions use temporary SQLite indexes instead of loading source rows into memory.

## Install

Python 3.10+ is required. The project has no third-party runtime dependencies.

```bash
python3 -m pip install .
mosaic-data-merger validate --config /path/to/config/job.json
```

For development, use `python3 -m pip install -e .`. You can also run from a checkout without installing it: `PYTHONPATH=src python3 -m mosaic_data_merger …`.

## Run a job

```bash
mosaic-data-merger validate --config config/job.json
mosaic-data-merger run --config config/job.json --verbose
mosaic-data-merger run --config config/job.json --dry-run
```

Configuration paths are relative to the configuration file. A conventional layout is:

```text
project/
├── config/job.json
├── input/
└── output/
```

In that layout, use `../input/*.json` for inputs and `../output/result.csv` for output in `config/job.json`. Wildcards work for every supported input type; use them with `validate` or `run`, not `inspect` (which accepts one file).

The `inspect` command helps identify a single unfamiliar file’s delimiter, likely header, and sample fields:

```bash
mosaic-data-merger inspect input/source.csv --delimiter ','
mosaic-data-merger inspect input/threat-intelligence.json --format stix
```

Commands print machine-readable JSON. Add `--verbose` for progress information on standard error, and `--report /path/to/report.json` to persist a run summary.

## Documentation and examples

- [Configuration reference](CONFIGURATION.md) — complete option reference, format rules, defaults, error behavior, and copyable recipes.
- [CLI output and reports](CLI_OUTPUT.md) — exact JSON output fields, counters, exit codes, diagnostics, and automation examples.
- [Architecture](ARCHITECTURE.md) — package structure and extension points.
- [Examples](examples) — sample jobs, data, and a comprehensive [configuration catalog](examples/config-catalog.json).

The two standalone job samples use configuration-relative `../input/` and `../output/` paths. The catalog references the bundled `examples/data` inputs.

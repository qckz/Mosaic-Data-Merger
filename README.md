# Mosaic Data Merger

Combine data from multiple files into one consistent output.

Mosaic Data Merger brings together records from files that use different formats, column names, and layouts. Combine CSV, JSON, JSONL, and STIX files in the same job, map their fields to a shared set of output columns, and write the combined result in your chosen format. For example, a column called `City` in one CSV and a key called `location` in a JSON file can both populate the `Place` column in the output.

A JSON configuration defines which files to combine, how their fields correspond, and how to process their values. You can select entire groups of files using wildcard filenames, clean and transform values, fill missing fields with defaults, remove duplicates, and exclude records that match an exclusion list. Mosaic can also transform or convert a single file using the same configuration options.

Built for large datasets and repeatable automation, Mosaic runs from the command line and processes your data locally. Each run produces a report showing how many records were read, written, filtered, excluded, or rejected, so you can track what happened to your data.

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
- [Roadmap](ROADMAP.md) — planned improvements, including nested JSON paths and explicit array handling.
- [Architecture](ARCHITECTURE.md) — package structure and extension points.
- [Examples](examples) — sample jobs, data, and a comprehensive [configuration catalog](examples/config-catalog.json).

The two standalone job samples use configuration-relative `../input/` and `../output/` paths. The catalog references the bundled `examples/data` inputs.

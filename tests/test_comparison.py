import copy
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mosaic_data_merger import ConfigError, RowError, compare, validate_comparison
from mosaic_data_merger.cli import main


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def source(self, side, kind, records):
        path = self.directory / f"{side}.{kind}"
        if kind == "csv":
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["id", "name"])
                writer.writeheader()
                writer.writerows(records)
        elif kind == "jsonl":
            path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        elif kind == "json":
            path.write_text(json.dumps(records), encoding="utf-8")
        else:
            objects = [
                {"type": "note", "id": f"note--{index}", "external_id": row["id"], "name": row["name"]}
                for index, row in enumerate(records)
            ]
            path.write_text(json.dumps({"type": "bundle", "id": "bundle--test", "objects": objects}), encoding="utf-8")
        return {
            "path": str(path), "format": kind,
            "mapping": {"external_id" if kind == "stix" else "id": "ID", "name": "Name"},
        }

    def job(self, left_kind="csv", right_kind="jsonl"):
        return {
            "_config_directory": str(self.directory),
            "left": self.source("left", left_kind, [
                {"id": "1", "name": "Left"}, {"id": "2", "name": "Old name"},
                {"id": "1", "name": "Duplicate"},
            ]),
            "right": self.source("right", right_kind, [
                {"id": "2", "name": "New name"}, {"id": "3", "name": "Right"},
            ]),
            "comparison": {"keys": ["ID"], "mode": "both"},
            "output": {"path": "out.json", "format": "json", "columns": ["ID", "Name"]},
        }

    def rows(self):
        return json.loads((self.directory / "out.json").read_text(encoding="utf-8"))

    def test_every_input_pair_preserves_duplicates_and_ignores_non_key_changes(self):
        for left in ("csv", "json", "jsonl", "stix"):
            for right in ("csv", "json", "jsonl", "stix"):
                with self.subTest(left=left, right=right):
                    report = compare(self.job(left, right))
                    self.assertEqual(self.rows(), [
                        {"ID": "1", "Name": "Left", "PresentIn": "left"},
                        {"ID": "1", "Name": "Duplicate", "PresentIn": "left"},
                        {"ID": "3", "Name": "Right", "PresentIn": "right"},
                    ])
                    self.assertEqual(report["rows_written"], 3)
                    self.assertEqual(report["left"]["rows_matched"], 1)
                    self.assertEqual(report["left"]["rows_missing"], 2)
                    self.assertEqual(report["right"]["rows_matched"], 1)
                    for side in ("left", "right"):
                        counts = report[side]
                        self.assertEqual(counts["rows_read"], counts["rows_matched"] + counts["rows_missing"] + counts["rows_skipped"])

    def test_one_direction_modes_and_optional_label(self):
        for mode, expected in (("left_only", ["1", "1"]), ("right_only", ["3"])):
            with self.subTest(mode=mode):
                config = self.job()
                config["comparison"]["mode"] = mode
                report = compare(config)
                self.assertEqual([row["ID"] for row in self.rows()], expected)
                self.assertTrue(all("PresentIn" not in row for row in self.rows()))
                self.assertEqual(report["left"]["rows_missing"], 2)
                self.assertEqual(report["right"]["rows_missing"], 1)
                config["output"]["side_column"] = "Source"
                compare(config)
                self.assertTrue(all(row["Source"] == mode.removesuffix("_only") for row in self.rows()))

    def test_composite_keys_use_and_and_keep_tuple_boundaries(self):
        config = self.job()
        config["comparison"]["keys"] = ["ID", "Name"]
        config["left"] = self.source("left", "json", [
            {"id": "a|b", "name": "c"}, {"id": "x", "name": "y"},
        ])
        config["right"] = self.source("right", "json", [
            {"id": "a", "name": "b|c"}, {"id": "x", "name": "changed"},
        ])
        report = compare(config)
        self.assertEqual(report["rows_written"], 4)

    def test_transformations_apply_to_both_sides(self):
        config = self.job()
        config["left"] = self.source("left", "json", [{"id": " ABC ", "name": "A"}])
        config["right"] = self.source("right", "jsonl", [{"id": "abc", "name": "B"}])
        self.assertEqual(compare(config)["rows_written"], 2)
        config["transformations"] = {"ID": [{"type": "trim"}, {"type": "lowercase"}]}
        self.assertEqual(compare(config)["rows_written"], 0)
        self.assertEqual(self.rows(), [])

    def test_empty_key_error_skip_and_allow(self):
        config = self.job()
        config["left"] = self.source("left", "json", [{"id": None, "name": "A"}])
        config["right"] = self.source("right", "json", [{"id": "", "name": "B"}])
        with self.assertRaisesRegex(RowError, "left.*empty value"):
            compare(config)
        config["comparison"]["on_empty_key"] = "skip"
        report = compare(config)
        for side in ("left", "right"):
            self.assertEqual(report[side]["rows_invalid_keys"], 1)
            self.assertEqual(report[side]["rows_skipped"], 1)
        self.assertEqual(self.rows(), [])
        config["comparison"].update(on_empty_key="error", allow_empty_keys=True)
        report = compare(config)
        self.assertEqual(report["left"]["rows_matched"], 1)
        self.assertEqual(report["rows_written"], 0)

    def test_headerless_wildcards_defaults_and_provenance(self):
        config = self.job()
        (self.directory / "part-02.csv").write_text("2\n", encoding="utf-8")
        (self.directory / "part-01.csv").write_text("1\n", encoding="utf-8")
        config["left"] = {
            "path": "part-*.csv", "header": False, "delimiter": ",",
            "mapping": {"1": "ID", "2": {"output_column": "Name", "default_if_missing": "fallback"}},
        }
        config["right"] = self.source("right", "json", [])
        config["output"]["add_provenance"] = True
        report = compare(config)
        self.assertEqual([row["ID"] for row in self.rows()], ["1", "2"])
        self.assertEqual(self.rows()[0]["Name"], "fallback")
        self.assertTrue(self.rows()[0]["source_file"].endswith("part-01.csv"))
        self.assertEqual(self.rows()[0]["source_row"], "1")
        self.assertEqual(report["left"]["matched_files"], 2)

    def test_malformed_rows_and_missing_keys_are_counted_separately(self):
        config = self.job()
        path = self.directory / "left.jsonl"
        path.write_text('bad json\n{"id":"","name":"Empty"}\n{"id":"1","name":"Good"}\n', encoding="utf-8")
        config["left"] = {
            "path": str(path), "format": "jsonl", "on_malformed_row": "skip",
            "mapping": {"id": "ID", "name": "Name"},
        }
        config["comparison"]["on_empty_key"] = "skip"
        report = compare(config)
        self.assertEqual(report["left"]["rows_read"], 3)
        self.assertEqual(report["left"]["rows_skipped"], 2)
        self.assertEqual(report["left"]["rows_invalid_keys"], 1)
        self.assertEqual(report["left"]["rows_missing"], 1)

    def test_csv_jsonl_and_stix_output(self):
        for kind in ("csv", "jsonl", "stix"):
            with self.subTest(output=kind):
                config = self.job()
                config["output"].update(format=kind, path="out." + kind)
                if kind == "csv":
                    config["output"].update(delimiter=";", quote_all=True)
                if kind == "stix":
                    config["output"]["stix"] = {
                        "object_type": "note",
                        "properties": {"Name": "content", "PresentIn": "x_present_in"},
                        "static": {"object_refs": ["identity--00000000-0000-4000-8000-000000000001"]},
                    }
                compare(config)
                output = self.directory / ("out." + kind)
                if kind == "csv":
                    with output.open(newline="") as handle:
                        rows = list(csv.DictReader(handle, delimiter=";"))
                    self.assertTrue(output.read_text().startswith('"ID";"Name";"PresentIn"'))
                elif kind == "jsonl":
                    rows = [json.loads(line) for line in output.read_text().splitlines()]
                else:
                    rows = json.loads(output.read_text())["objects"]
                    self.assertEqual([row["x_present_in"] for row in rows], ["left", "left", "right"])
                self.assertEqual(len(rows), 3)

    def test_dry_run_and_failed_run_preserve_existing_output(self):
        config = self.job()
        output = self.directory / "out.json"
        report = compare(config, dry_run=True)
        self.assertEqual(report["rows_written"], 3)
        self.assertFalse(output.exists())
        output.write_text("sentinel", encoding="utf-8")
        config["right"] = self.source("right", "jsonl", [{"id": "", "name": "empty"}])
        with self.assertRaises(RowError):
            compare(config)
        self.assertEqual(output.read_text(), "sentinel")
        self.assertEqual(list(self.directory.glob(".out.json.*.tmp")), [])

    def test_atomic_writer_failure_preserves_output(self):
        config = self.job()
        output = self.directory / "out.json"
        output.write_text("sentinel", encoding="utf-8")
        with patch("mosaic_data_merger.writers.JsonArrayWriter.writerow", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                compare(config)
        self.assertEqual(output.read_text(), "sentinel")
        self.assertEqual(list(self.directory.glob(".out.json.*.tmp")), [])

    def test_invalid_configuration_and_source_output_collision(self):
        baseline = self.job()
        changes = [
            lambda c: c["comparison"].update(mode="typo"),
            lambda c: c["comparison"].update(keys=[]),
            lambda c: c["comparison"].update(keys=["ID", "ID"]),
            lambda c: c["comparison"].update(keys=["Missing"]),
            lambda c: c["comparison"].update(allow_empty_keys="false"),
            lambda c: c["comparison"].update(on_empty_key="ignore"),
            lambda c: c["output"].update(side_column="Name"),
            lambda c: c["output"].update(side_column=None),
            lambda c: c["left"]["mapping"].pop("id"),
            lambda c: c.update(filters=[]),
            lambda c: c["output"].update(path=c["left"]["path"]),
        ]
        for change in changes:
            config = copy.deepcopy(baseline)
            change(config)
            with self.subTest(config=config), self.assertRaises(ConfigError):
                validate_comparison(config)

    def test_cli_validate_compare_report_and_errors(self):
        config = self.job()
        job_path = self.directory / "job.json"
        job_path.write_text(json.dumps(config), encoding="utf-8")
        report_path = self.directory / "report.json"
        for command in ("validate", "compare"):
            args = ["mosaic-data-merger", command, "--config", str(job_path)]
            if command == "compare":
                args += ["--dry-run", "--verbose", "--report", str(report_path)]
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("sys.argv", args), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(), 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["operation"], "compare")
            if command == "compare":
                self.assertEqual(report, json.loads(report_path.read_text()))
                self.assertIn("duration_seconds", report)
                self.assertIn("info:", stderr.getvalue())
                self.assertFalse((self.directory / "out.json").exists())
        with patch("sys.argv", ["mosaic-data-merger", "compare", "--config", str(job_path),
                                "--report", config["left"]["path"]]), redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 2)

    def test_append_output_and_header_mismatch(self):
        config = self.job()
        config["output"].update(path="out.csv", format="csv", mode="append")
        compare(config)
        compare(config)
        with (self.directory / "out.csv").open(newline="") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 6)
        config["output"]["side_column"] = "DifferentLabel"
        with self.assertRaisesRegex(ConfigError, "header differs"):
            compare(config)

    def test_empty_inputs_and_no_matching_wildcard(self):
        config = self.job()
        config["left"] = self.source("left", "csv", [])
        config["right"] = self.source("right", "jsonl", [])
        report = compare(config)
        self.assertEqual(self.rows(), [])
        self.assertEqual(report["left"]["rows_read"], 0)
        config["left"]["path"] = "absent-*.csv"
        with self.assertRaisesRegex(ConfigError, "matched no files"):
            validate_comparison(config)

    def test_jsonl_append_requires_newline(self):
        config = self.job()
        config["output"].update(format="jsonl", path="out.jsonl", mode="append")
        output = self.directory / "out.jsonl"
        output.write_text('{"ID":"existing"}', encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "newline"):
            compare(config)
        self.assertEqual(output.read_text(), '{"ID":"existing"}')
        output.write_text('{"ID":"existing"}\n', encoding="utf-8")
        compare(config)
        self.assertEqual(len([json.loads(line) for line in output.read_text().splitlines()]), 4)

    def test_stix_label_must_map_to_custom_property(self):
        config = self.job()
        config["output"].update(format="stix", stix={"object_type": "note", "properties": {}})
        with self.assertRaisesRegex(ConfigError, "x_ custom property"):
            validate_comparison(config)

    def test_validation_does_not_mutate_configuration(self):
        config = self.job()
        before = copy.deepcopy(config)
        validate_comparison(config)
        compare(config, dry_run=True)
        self.assertEqual(config, before)

    def test_cli_protects_configuration_file(self):
        config = self.job()
        path = self.directory / "job.json"
        config["output"]["path"] = str(path)
        original = json.dumps(config)
        path.write_text(original, encoding="utf-8")
        with patch("sys.argv", ["mosaic-data-merger", "compare", "--config", str(path)]), redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 2)
        self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()

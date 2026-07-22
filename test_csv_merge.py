import csv
import json
import tempfile
import unittest
from pathlib import Path

import csv_merge


class CsvMergeTests(unittest.TestCase):
    def make_job(self, directory: Path) -> Path:
        (directory / "no-header.csv").write_text(
            "amsterdam,Alice,unused,19-07-2026\nrotterdam,,unused,20-07-2026\n",
            encoding="utf-8",
        )
        (directory / "headed.csv").write_text(
            "City;Customer;When\nAMSTERDAM;Alice;2026/07/19\nutrecht;Bob;invalid\n",
            encoding="utf-8",
        )
        job = {
            "output": {
                "path": "out/result.csv",
                "delimiter": ";",
                "columns": ["Place", "Name", "Date"],
                "add_provenance": True,
                "atomic_write": True,
            },
            "inputs": [
                {
                    "path": "no-header.csv",
                    "header": False,
                    "delimiter": ",",
                    "mapping": {"1": "Place", "2": "Name", "4": "Date"},
                    "on_malformed_row": "pad",
                },
                {
                    "path": "headed.csv",
                    "header": True,
                    "delimiter": ";",
                    "mapping": {"City": "Place", "Customer": "Name", "When": "Date"},
                },
            ],
            "transformations": {
                "Place": [{"type": "trim"}, {"type": "title_case"}],
                "Date": [{"type": "date_format", "input_formats": ["%d-%m-%Y", "%Y/%m/%d"], "output_format": "%Y-%m-%d"}],
            },
            "filters": [{"column": "Name", "operator": "not_empty"}],
            "validation": {
                "on_error": "reject",
                "rejects_path": "out/rejects.csv",
                "rules": [{"column": "Date", "type": "date", "format": "%Y-%m-%d"}],
            },
            "deduplication": {"enabled": True, "keys": ["Place", "Name"], "keep": "first"},
        }
        config_path = directory / "job.json"
        config_path.write_text(json.dumps(job), encoding="utf-8")
        return config_path

    def test_mixed_layouts_validation_rejects_and_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            report = csv_merge.process(csv_merge.load_config(self.make_job(directory)))
            self.assertEqual(report["rows_read"], 4)
            self.assertEqual(report["rows_written"], 1)
            self.assertEqual(report["rows_filtered"], 1)
            self.assertEqual(report["rows_rejected"], 1)
            self.assertEqual(report["rows_deduplicated"], 1)
            with (directory / "out/result.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(rows, [{"Place": "Amsterdam", "Name": "Alice", "Date": "2026-07-19", "source_file": str((directory / "no-header.csv").resolve()), "source_row": "1"}])
            with (directory / "out/rejects.csv").open(newline="", encoding="utf-8") as handle:
                rejected = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(len(rejected), 1)
            self.assertIn("cannot parse date", rejected[0]["_error"])

    def test_validate_rejects_name_mapping_without_header(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "source.csv").write_text("value\n", encoding="utf-8")
            config = {
                "output": {"path": "result.csv", "columns": ["Value"]},
                "inputs": [{"path": "source.csv", "header": False, "mapping": {"Value": "Value"}}],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(csv_merge.ConfigError):
                csv_merge.process(csv_merge.load_config(config_path))

    def test_one_jsonl_file_transforms_all_mapped_keys_to_csv(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "people.jsonl").write_text(
                '{"city":"amsterdam","full_name":"Alice","active":true,"tags":["vip","new"]}\n'
                '{"city":"utrecht","full_name":"Bob","active":false,"tags":null}\n',
                encoding="utf-8",
            )
            config = {
                "output": {"path": "out/people.csv", "delimiter": ";", "columns": ["Place", "Name", "Active", "Tags"]},
                "inputs": [{
                    "path": "people.jsonl",
                    "format": "jsonl",
                    "mapping": {"city": "Place", "full_name": "Name", "active": "Active", "tags": "Tags"},
                }],
                "transformations": {"Place": [{"type": "title_case"}]},
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            report = csv_merge.process(csv_merge.load_config(config_path))
            self.assertEqual(report["rows_read"], 2)
            self.assertEqual(report["rows_written"], 2)
            self.assertEqual(report["files"][0]["format"], "jsonl")
            with (directory / "out/people.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(rows, [
                {"Place": "Amsterdam", "Name": "Alice", "Active": "true", "Tags": '["vip","new"]'},
                {"Place": "Utrecht", "Name": "Bob", "Active": "false", "Tags": ""},
            ])


if __name__ == "__main__":
    unittest.main()

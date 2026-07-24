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

    def test_json_input_and_output_use_a_json_object_list(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "people.json").write_text(
                json.dumps([{"city": "amsterdam", "name": "Alice"}, {"city": "utrecht", "name": "Bob"}]),
                encoding="utf-8",
            )
            config = {
                "output": {"path": "out/people.json", "format": "json", "columns": ["Place", "Name"]},
                "inputs": [{"path": "people.json", "format": "json", "mapping": {"city": "Place", "name": "Name"}}],
                "transformations": {"Place": [{"type": "title_case"}]},
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            report = csv_merge.process(csv_merge.load_config(config_path))
            self.assertEqual(report["rows_written"], 2)
            self.assertEqual(
                json.loads((directory / "out/people.json").read_text(encoding="utf-8")),
                [{"Place": "Amsterdam", "Name": "Alice"}, {"Place": "Utrecht", "Name": "Bob"}],
            )

    def test_stix_bundle_input_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            stix_input = {
                "type": "bundle",
                "id": "bundle--00000000-0000-4000-8000-000000000001",
                "objects": [{
                    "type": "indicator",
                    "spec_version": "2.1",
                    "id": "indicator--00000000-0000-4000-8000-000000000002",
                    "created": "2026-07-22T12:00:00.000Z",
                    "modified": "2026-07-22T12:00:00.000Z",
                    "name": "Suspicious domain",
                    "pattern": "[domain-name:value = 'example.test']",
                    "pattern_type": "stix",
                    "valid_from": "2026-07-22T12:00:00.000Z",
                }],
            }
            (directory / "input.stix").write_text(json.dumps(stix_input), encoding="utf-8")
            config = {
                "output": {
                    "path": "out/notes.json",
                    "format": "stix",
                    "columns": ["Name", "Pattern"],
                    "stix": {
                        "object_type": "note",
                        "properties": {"Name": "abstract", "Pattern": "content"},
                        "static": {"object_refs": ["indicator--00000000-0000-4000-8000-000000000002"]},
                        "timestamp": "2026-07-22T12:00:00.000Z",
                    },
                },
                "inputs": [{
                    "path": "input.stix",
                    "format": "stix",
                    "mapping": {"name": "Name", "pattern": "Pattern"},
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            report = csv_merge.process(csv_merge.load_config(config_path))
            self.assertEqual(report["rows_written"], 1)
            bundle = json.loads((directory / "out/notes.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["type"], "bundle")
            self.assertTrue(bundle["id"].startswith("bundle--"))
            self.assertEqual(bundle["objects"][0]["type"], "note")
            self.assertEqual(bundle["objects"][0]["abstract"], "Suspicious domain")
            self.assertEqual(bundle["objects"][0]["content"], "[domain-name:value = 'example.test']")

    def test_wildcard_input_streams_matching_files_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "list_02.csv").write_text("Utrecht,Bob\n", encoding="utf-8")
            (directory / "list_01.csv").write_text("Amsterdam,Alice\n", encoding="utf-8")
            config = {
                "output": {
                    "path": "out/merged.csv",
                    "columns": ["Place", "Name"],
                    "add_provenance": True,
                },
                "inputs": [{
                    "path": "list_*.csv",
                    "header": False,
                    "mapping": {"1": "Place", "2": "Name"},
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            information: list[str] = []
            report = csv_merge.process(csv_merge.load_config(config_path), info=information.append)
            self.assertEqual(report["rows_read"], 2)
            self.assertEqual(report["input_patterns"], [{"path": "list_*.csv", "format": "csv", "matched_files": 2}])
            self.assertTrue(any("matched 2 file(s)" in message for message in information))
            self.assertTrue(any("delimiter=','" in message for message in information))
            self.assertEqual([item["path"] for item in report["files"]], [
                str((directory / "list_01.csv").resolve()),
                str((directory / "list_02.csv").resolve()),
            ])
            with (directory / "out/merged.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["Place"] for row in rows], ["Amsterdam", "Utrecht"])

    def test_wildcard_without_matches_is_a_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            config = {
                "output": {"path": "out.csv", "columns": ["Value"]},
                "inputs": [{"path": "missing_*.csv", "header": False, "mapping": {"1": "Value"}}],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(csv_merge.ConfigError, "matched no files"):
                csv_merge.process(csv_merge.load_config(config_path))

    def test_wrong_csv_delimiter_explains_the_parsed_header(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "people.csv").write_text("City,Name\nAmsterdam,Alice\n", encoding="utf-8")
            config = {
                "output": {"path": "out.csv", "columns": ["Place", "Name"]},
                "inputs": [{
                    "path": "people.csv",
                    "header": True,
                    "delimiter": ";",
                    "mapping": {"City": "Place", "Name": "Name"},
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(csv_merge.ConfigError, "delimiter ';'.*Available headers: 'City,Name'.*contains ','"):
                csv_merge.process(csv_merge.load_config(config_path))

    def test_input_mapping_constants_fill_output_columns(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "places.csv").write_text("City\nAmsterdam\n", encoding="utf-8")
            config = {
                "output": {"path": "out.csv", "columns": ["Place", "Source", "Priority"]},
                "inputs": [{
                    "path": "places.csv",
                    "header": True,
                    "mapping": {
                        "City": "Place",
                        "$constants": {"Source": "manual-import", "Priority": 10},
                    },
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            csv_merge.process(csv_merge.load_config(config_path))
            with (directory / "out.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [{"Place": "Amsterdam", "Source": "manual-import", "Priority": "10"}])

    def test_quote_all_quotes_every_csv_field_and_escapes_embedded_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "people.jsonl").write_text(
                '{"name":"Alice \\"The Ace\\"","city":"Amsterdam"}\n', encoding="utf-8"
            )
            config = {
                "output": {
                    "path": "out.csv",
                    "columns": ["Name", "Place"],
                    "quote_all": True,
                },
                "inputs": [{
                    "path": "people.jsonl",
                    "format": "jsonl",
                    "mapping": {"name": "Name", "city": "Place"},
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            csv_merge.process(csv_merge.load_config(config_path))
            self.assertEqual(
                (directory / "out.csv").read_text(encoding="utf-8"),
                '"Name","Place"\n"Alice ""The Ace""","Amsterdam"\n',
            )

    def test_headerless_exclusion_list_uses_all_keys_for_and_matching(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "people.csv").write_text(
                "Email,Country,Name\nalice@example.test,NL,Alice NL\nalice@example.test,US,Alice US\nbob@example.test,NL,Bob NL\n",
                encoding="utf-8",
            )
            (directory / "exclude.csv").write_text("alice@example.test,NL\n", encoding="utf-8")
            config = {
                "output": {"path": "out/people.json", "format": "json", "columns": ["Email", "Country", "Name"]},
                "inputs": [{
                    "path": "people.csv",
                    "header": True,
                    "mapping": {"Email": "Email", "Country": "Country", "Name": "Name"},
                }],
                "exclusions": [{
                    "path": "exclude.csv",
                    "header": False,
                    "mapping": {"1": "Email", "2": "Country"},
                    "keys": ["Email", "Country"],
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            report = csv_merge.process(csv_merge.load_config(config_path))
            self.assertEqual(report["rows_excluded"], 1)
            self.assertEqual(report["exclusions"][0]["keys_loaded"], 1)
            self.assertEqual(report["exclusions"][0]["rows_excluded"], 1)
            self.assertEqual(
                json.loads((directory / "out/people.json").read_text(encoding="utf-8")),
                [
                    {"Email": "alice@example.test", "Country": "US", "Name": "Alice US"},
                    {"Email": "bob@example.test", "Country": "NL", "Name": "Bob NL"},
                ],
            )

    def test_exclusion_key_must_be_mapped(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "people.csv").write_text("Email\na@example.test\n", encoding="utf-8")
            (directory / "exclude.csv").write_text("a@example.test\n", encoding="utf-8")
            config = {
                "output": {"path": "out.csv", "columns": ["Email", "Country"]},
                "inputs": [{"path": "people.csv", "mapping": {"Email": "Email"}}],
                "exclusions": [{
                    "path": "exclude.csv",
                    "header": False,
                    "mapping": {"1": "Email"},
                    "keys": ["Email", "Country"],
                }],
            }
            config_path = directory / "job.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(csv_merge.ConfigError, "keys must each be mapped"):
                csv_merge.process(csv_merge.load_config(config_path))


if __name__ == "__main__":
    unittest.main()

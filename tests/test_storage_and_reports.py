"""Check result-file recovery and the reports made from saved runs."""

import csv
import io
import json

import detectors
import report_generator
import scoring
import storage
from test_detectors import make_record


def test_missing_results_file_means_no_tests():
    assert not storage.results_path().exists()
    assert storage.load_results() == ([], None)


def test_empty_results_file_means_no_tests():
    storage.results_path().write_text("", encoding="utf-8")
    assert storage.load_results() == ([], None)


def test_malformed_results_file_is_backed_up_not_crashed():
    storage.results_path().write_text("{ this is not json", encoding="utf-8")
    records, warning = storage.load_results()
    assert records == []
    assert "could not be read" in warning
    backups = list(storage.data_dir().glob("results.corrupt-*.json"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "{ this is not json"


def test_incomplete_records_are_skipped_with_warning():
    good = make_record()
    storage.save_results([good, {"test_id": "half a record"}, "junk"])
    records, warning = storage.load_results()
    assert [r["test_id"] for r in records] == [good["test_id"]]
    assert "2 record(s)" in warning


def test_serialization_round_trip():
    record = make_record("upload", "slow_network", "TIMEOUT", receipts=1)
    storage.append_result(record)
    storage.append_result(make_record(test_id="T-2"))
    records, warning = storage.load_results()
    assert warning is None
    assert records[0] == json.loads(json.dumps(record))
    assert len(records) == 2


def test_assumptions_fall_back_to_defaults_and_save():
    assert storage.load_assumptions(scoring.DEFAULT_ASSUMPTIONS) == scoring.DEFAULT_ASSUMPTIONS
    changed = storage.load_assumptions(scoring.DEFAULT_ASSUMPTIONS)
    changed["likelihood"]["slow_network"] = 2
    storage.save_assumptions(changed)
    assert storage.load_assumptions(scoring.DEFAULT_ASSUMPTIONS)["likelihood"]["slow_network"] == 2
    assert scoring.DEFAULT_ASSUMPTIONS["likelihood"]["slow_network"] == 4  # Saving edits must not change the defaults.


def test_csv_export_contains_actual_results():
    records = [make_record(test_id="T-A"), make_record("upload", "slow_network", "TIMEOUT", receipts=2,
                                                       retries=1, test_id="T-B")]
    rows = list(csv.DictReader(io.StringIO(report_generator.results_csv(records))))
    assert [r["test_id"] for r in rows] == ["T-A", "T-B"]
    assert rows[1]["operation_status"] == "TIMEOUT"
    assert rows[1]["server_receipt_count"] == "2"
    assert rows[1]["timeout"] == "1.0" and rows[1]["server_delay"] == "3.0"


def test_json_and_findings_exports_contain_actual_results():
    records = [make_record("upload", "slow_network", "TIMEOUT", receipts=2, retries=1, test_id="T-B")]
    findings = detectors.build_findings(records, scoring.DEFAULT_ASSUMPTIONS)
    report = json.loads(report_generator.full_json(records, findings, scoring.DEFAULT_ASSUMPTIONS))
    assert report["test_count"] == 1
    assert report["results"][0]["test_id"] == "T-B"
    assert {f["code"] for f in report["findings"]} >= {"DUPLICATE_UPLOAD", "AMBIGUOUS_UPLOAD"}
    finding_rows = list(csv.DictReader(io.StringIO(report_generator.findings_csv(findings))))
    assert len(finding_rows) == len(findings)
    markdown = report_generator.findings_markdown(findings, scoring.DEFAULT_ASSUMPTIONS)
    assert "python tester.py --operation upload" in markdown

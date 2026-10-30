"""Unit tests for the detector rules and finding creation (no network needed)."""

import detectors
import scoring
import tester


def make_record(operation="load_data", profile="normal", status="SUCCESS", receipts=None,
                test_id="T-1", sha_match=True, **config_changes):
    """Build a hand-made test record in the same shape tester.run_test() produces."""
    config = tester.build_config(operation, profile, **config_changes)
    evidence = {"attempts_made": config["retries"] + 1, "simulated_drops": 0,
                "final_http_status": 503 if status == "HTTP_ERROR" else 200}
    if operation == "upload":
        evidence["upload_id"] = "U-1"
        evidence["client_sha256"] = "abc"
        evidence["server_check"] = {"checked": True, "receipt_count": receipts,
                                    "server_sha256": ["abc" if sha_match else "xyz"] * receipts,
                                    "receipt_ids": [f"R-{i}" for i in range(receipts)]}
    record = {
        "test_id": test_id, "timestamp": "2026-10-30T12:00:00", "profile": profile,
        "profile_label": tester.PROFILES[profile]["label"], "operation": operation,
        "operation_label": tester.OPERATIONS[operation]["label"], "config": config,
        "evidence_category": tester.evidence_category(config), "expected_status": status,
        "expected_outcome": "", "operation_status": status, "verdict": "PASS", "duration_s": 1.0,
        "error": {"type": "ReadTimeout", "message": "timed out", "http_status": None}
        if status == "TIMEOUT" else None,
        "attempts": [], "evidence": evidence, "actual_outcome": "test",
    }
    record["detections"] = detectors.detect(record)
    record["finding_ids"] = [d["finding_id"] for d in record["detections"]]
    return record


def codes(record):
    return sorted(d["code"] for d in record["detections"])


def test_success_produces_no_findings():
    assert codes(make_record()) == []


def test_timeout_is_detected():
    record = make_record(profile="slow_network", status="TIMEOUT")
    assert codes(record) == ["TIMEOUT"]
    assert "ReadTimeout" in record["detections"][0]["explanation"]


def test_http_error_is_detected():
    assert codes(make_record(status="HTTP_ERROR", fail_count=1)) == ["HTTP_FAILURE"]


def test_failed_retries_trigger_recovery_failure():
    record = make_record(profile="intermittent", status="HTTP_ERROR")  # profile default retries = 1
    assert codes(record) == ["HTTP_FAILURE", "RECOVERY_FAILURE"]


def test_ambiguous_upload_when_timeout_but_server_saved():
    record = make_record("upload", "slow_network", "TIMEOUT", receipts=1)
    assert codes(record) == ["AMBIGUOUS_UPLOAD", "TIMEOUT"]


def test_duplicate_upload_when_server_saved_twice():
    record = make_record("upload", "slow_network", "TIMEOUT", receipts=2, retries=1)
    assert "DUPLICATE_UPLOAD" in codes(record)


def test_upload_not_saved_when_nothing_reached_server():
    record = make_record("upload", "intermittent", "HTTP_ERROR", receipts=0)
    assert "UPLOAD_NOT_SAVED" in codes(record)


def test_upload_integrity_when_hash_differs():
    record = make_record("upload", "normal", "SUCCESS", receipts=1, sha_match=False)
    assert codes(record) == ["UPLOAD_INTEGRITY"]


def test_successful_upload_saved_once_has_no_findings():
    assert codes(make_record("upload", "normal", "SUCCESS", receipts=1)) == []


def test_simulated_loss_is_labelled_simulated():
    record = make_record(profile="packet_loss", status="LOST_SIMULATED", retries=0)
    assert record["evidence_category"] == "SIMULATED"
    assert codes(record) == ["REQUEST_LOST_SIMULATED"]


def test_finding_ids_are_stable():
    a = make_record(profile="slow_network", status="TIMEOUT", test_id="T-1")
    b = make_record(profile="slow_network", status="TIMEOUT", test_id="T-2")
    assert a["finding_ids"] == b["finding_ids"]


def test_repeated_failures_group_into_one_finding_with_original_config():
    first = make_record(profile="slow_network", status="TIMEOUT", test_id="T-1", server_delay=3.0)
    second = make_record(profile="slow_network", status="TIMEOUT", test_id="T-2", server_delay=5.0)
    ok = make_record(profile="slow_network", status="SUCCESS", test_id="T-3", server_delay=0.5)
    findings = detectors.build_findings([first, second, ok], scoring.DEFAULT_ASSUMPTIONS)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["occurrences"] == 2
    assert finding["original_test_id"] == "T-1"
    assert finding["original_config"]["server_delay"] == 3.0  # T7: original parameters retained
    assert finding["test_ids"] == ["T-1", "T-2"]


def test_findings_sorted_by_priority():
    records = [make_record(profile="slow_network", status="TIMEOUT", test_id="T-1"),
               make_record("upload", "slow_network", "TIMEOUT", receipts=2, retries=1, test_id="T-2")]
    findings = detectors.build_findings(records, scoring.DEFAULT_ASSUMPTIONS)
    percents = [f["priority"]["percent"] for f in findings]
    assert percents == sorted(percents, reverse=True)
    assert findings[0]["code"] == "DUPLICATE_UPLOAD"


def test_reproduction_command_parses_back_to_same_config():
    config = tester.build_config("upload", "slow_network", retries=1, idempotent_server=True, seed=7)
    command = detectors.reproduction_command(config)
    args = tester.make_parser().parse_args(command.split()[2:])  # drop "python tester.py"
    assert tester.config_from_args(args) == config


def test_no_curl_reproduction_for_simulation():
    assert detectors.curl_commands(tester.build_config("load_data", "packet_loss")) is None
    assert detectors.curl_commands(tester.build_config("load_data", "slow_network"))

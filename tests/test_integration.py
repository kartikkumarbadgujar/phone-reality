"""End-to-end tests: real HTTP requests against a real demo app started by conftest.py.

Delays are kept short (about 1 s) so the whole file runs in roughly 15 seconds.
"""

import requests

import storage
import tester


def run(base_url, operation, profile, **changes):
    return tester.run_test(tester.build_config(operation, profile, base_url, **changes))


def test_server_starts_and_responds(demo_server):  # T1
    ok, message = tester.check_server(demo_server)
    assert ok, message
    assert requests.get(demo_server + "/api/health", timeout=2).json()["status"] == "ok"


def test_normal_request_succeeds(demo_server):  # T2
    record = run(demo_server, "load_data", "normal")
    assert record["operation_status"] == "SUCCESS"
    assert record["verdict"] == "PASS"
    assert record["evidence_category"] == "GENUINE"
    assert record["detections"] == []


def test_slow_endpoint_gives_real_timeout(demo_server):  # T3
    record = run(demo_server, "load_data", "slow_network", timeout=0.5, server_delay=1.5)
    assert record["operation_status"] == "TIMEOUT"
    assert record["error"]["type"] == "ReadTimeout"            # a genuine requests exception
    assert 0.45 <= record["attempts"][0]["elapsed_s"] < 1.4      # gave up near the timeout, not the delay
    assert record["verdict"] == "PASS"
    assert record["evidence_category"] == "FAULT_INJECTION"
    assert [d["code"] for d in record["detections"]] == ["TIMEOUT"]


def test_normal_upload_is_received_and_recorded(demo_server):  # T4
    record = run(demo_server, "upload", "normal", upload_size_kb=16)
    check = record["evidence"]["server_check"]
    assert record["operation_status"] == "SUCCESS"
    assert check["receipt_count"] == 1
    assert check["server_sha256"] == [record["evidence"]["client_sha256"]]
    assert record["detections"] == []


def test_delayed_ack_upload_times_out_but_is_saved(demo_server):  # T5 - the main demonstration
    record = run(demo_server, "upload", "slow_network", timeout=0.5, server_delay=1.5)
    assert record["operation_status"] == "TIMEOUT"
    assert record["evidence"]["server_check"]["receipt_count"] == 1
    assert {"AMBIGUOUS_UPLOAD", "TIMEOUT"} == {d["code"] for d in record["detections"]}


def test_retry_after_timeout_creates_real_duplicate(demo_server):
    record = run(demo_server, "upload", "slow_network", timeout=0.5, server_delay=1.5, retries=1)
    assert record["evidence"]["server_check"]["receipt_count"] == 2
    assert "DUPLICATE_UPLOAD" in {d["code"] for d in record["detections"]}


def test_idempotent_server_prevents_duplicate(demo_server):
    record = run(demo_server, "upload", "slow_network", timeout=0.5, server_delay=1.5, retries=1,
                 idempotent_server=True)
    assert record["evidence"]["server_check"]["receipt_count"] == 1
    assert "DUPLICATE_UPLOAD" not in {d["code"] for d in record["detections"]}


def test_controlled_503_is_detected(demo_server):
    record = run(demo_server, "load_data", "intermittent")  # 2 failures, 1 retry
    assert record["operation_status"] == "HTTP_ERROR"
    assert record["evidence"]["final_http_status"] == 503
    assert {"HTTP_FAILURE", "RECOVERY_FAILURE"} == {d["code"] for d in record["detections"]}


def test_enough_retries_recover_from_503(demo_server):
    record = run(demo_server, "load_data", "intermittent", retries=2)
    assert record["operation_status"] == "SUCCESS"
    assert len(record["attempts"]) == 3
    assert record["detections"] == []


def test_upload_503_never_reaches_storage(demo_server):
    record = run(demo_server, "upload", "intermittent", retries=0, fail_count=1)
    assert record["evidence"]["server_check"]["receipt_count"] == 0
    assert "UPLOAD_NOT_SAVED" in {d["code"] for d in record["detections"]}


def test_simulated_packet_loss_is_deterministic_and_labelled(demo_server):
    lost = run(demo_server, "load_data", "packet_loss")            # seed 4: both attempts dropped
    assert lost["operation_status"] == "LOST_SIMULATED"
    assert lost["evidence"]["requests_sent_to_server"] == 0
    assert lost["evidence_category"] == "SIMULATED"
    recovered = run(demo_server, "load_data", "packet_loss", seed=1)  # seed 1: only the first is dropped
    assert recovered["operation_status"] == "SUCCESS"
    assert [a["status"] for a in recovered["attempts"]] == ["LOST_SIMULATED", "SUCCESS"]


def test_repeated_controlled_test_is_consistent(demo_server):  # T11
    config = tester.build_config("upload", "slow_network", demo_server, timeout=0.5, server_delay=1.2)
    records = [tester.run_test(config) for _ in range(3)]
    assert {r["operation_status"] for r in records} == {"TIMEOUT"}
    assert len({tuple(r["finding_ids"]) for r in records}) == 1
    assert len({r["evidence"]["client_sha256"] for r in records}) == 1  # same seed -> same file


def test_results_are_saved_and_reload(demo_server):
    run(demo_server, "load_data", "normal")
    run(demo_server, "load_data", "slow_network", timeout=0.5, server_delay=1.5)
    records, warning = storage.load_results()
    assert warning is None
    assert [r["operation_status"] for r in records] == ["SUCCESS", "TIMEOUT"]


def test_unavailable_server_gives_clear_error_and_saves_nothing(free_port):  # T13
    url = f"http://127.0.0.1:{free_port}"
    ok, message = tester.check_server(url, timeout=1.0)
    assert not ok
    assert "python demo_app.py" in message
    record = run(url, "load_data", "normal", timeout=1.0)
    assert record["verdict"] == "ERROR"
    assert record["detections"] == []
    assert storage.load_results() == ([], None)


def test_prediction_flags_timing_too_close_to_call():
    config = tester.build_config("load_data", "slow_network", timeout=1.0, server_delay=1.1)
    assert tester.predict_status(config)[0] == "UNPREDICTABLE"

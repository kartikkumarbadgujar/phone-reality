"""Guardrails for local requests, upload handling and saved run data."""

import io
from concurrent.futures import ThreadPoolExecutor

import demo_app
import storage
import tester


def test_demo_backend_rejects_remote_and_cross_site_clients(tmp_path):
    client = demo_app.create_app(tmp_path).test_client()
    remote = client.get("/api/health", environ_base={"REMOTE_ADDR": "203.0.113.5"})
    cross_site = client.get("/api/health", headers={"Origin": "https://attacker.example"})
    assert remote.status_code == 403
    assert cross_site.status_code == 403


def test_demo_backend_bounds_fault_inputs(tmp_path):
    client = demo_app.create_app(tmp_path).test_client()
    bad_delay = client.get("/api/data?delay=nan")
    bad_count = client.get("/api/data?fail_count=999999&fault_key=x")
    bad_key = client.get("/api/data?fail_count=1&fault_key=invalid%20key")
    assert [bad_delay.status_code, bad_count.status_code, bad_key.status_code] == [400, 400, 400]


def test_demo_backend_caps_request_size_and_upload_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_app, "MAX_STORED_BYTES", 4)
    client = demo_app.create_app(tmp_path).test_client()
    oversized_body = client.post("/api/upload", data={
        "upload_id": "bounded-body", "file": (io.BytesIO(b"x" * (demo_app.MAX_REQUEST_BYTES + 1)), "file.bin")
    })
    assert oversized_body.status_code == 413
    too_large = client.post("/api/upload", data={
        "upload_id": "bounded-1", "file": (io.BytesIO(b"12345"), "file.bin")
    })
    assert too_large.status_code == 507


def test_idempotency_key_cannot_claim_success_for_different_content(tmp_path):
    client = demo_app.create_app(tmp_path).test_client()
    first = client.post("/api/upload?idempotent=1", data={
        "upload_id": "same-key", "file": (io.BytesIO(b"first"), "file.bin")
    })
    duplicate = client.post("/api/upload?idempotent=1", data={
        "upload_id": "same-key", "file": (io.BytesIO(b"other"), "file.bin")
    })
    assert first.status_code == 200
    assert duplicate.status_code == 409


def test_tester_refuses_non_loopback_targets():
    for url in ("http://169.254.169.254", "http://192.168.1.2", "https://127.0.0.1", "http://user:pass@localhost"):
        try:
            tester.build_config("load_data", "normal", base_url=url)
        except ValueError:
            continue
        raise AssertionError(f"unsafe target accepted: {url}")


def test_tester_rejects_malformed_numeric_settings():
    for overrides in ({"timeout": float("nan")}, {"fail_count": True}, {"retries": "2"}):
        try:
            tester.build_config("load_data", "normal", **overrides)
        except ValueError:
            continue
        raise AssertionError(f"invalid settings accepted: {overrides}")


def test_malformed_assumptions_fall_back_to_defaults(tmp_path):
    defaults = {"likelihood": {"normal": 2}, "impact_weight": {"upload": 3}}
    path = tmp_path / "assumptions.json"
    path.write_text('["wrong shape"]', encoding="utf-8")
    assert storage.load_assumptions(defaults, path) == defaults
    path.write_text('{"likelihood": null, "impact_weight": []}', encoding="utf-8")
    assert storage.load_assumptions(defaults, path) == defaults


def test_json_result_appends_are_not_lost_between_threads(tmp_path):
    path = tmp_path / "results.json"
    def append(index):
        record = {
            "test_id": f"test-{index}", "timestamp": "now", "profile": "normal",
            "profile_label": "Normal", "operation": "load_data", "operation_label": "load",
            "config": {}, "evidence_category": "GENUINE", "expected_status": "SUCCESS",
            "operation_status": "SUCCESS", "verdict": "PASS", "duration_s": 0.1,
            "attempts": [], "evidence": {}, "actual_outcome": "ok", "detections": [],
        }
        storage.append_result(record, path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(24)))
    records, warning = storage.load_results(path)
    assert warning is None
    assert len(records) == 24

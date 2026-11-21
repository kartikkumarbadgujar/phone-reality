"""Coverage for the BudgetBench additions, using temporary SQLite state and mocked ADB output."""

import json

from core import adb_controller
from database import db_manager


def test_projects_snapshots_and_challenge_data_persist(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDGETBENCH_DB", str(tmp_path / "budgetbench.sqlite3"))
    project_id = db_manager.save_project("Checkout", "Reliability", "REST API", "http://127.0.0.1:5050", environment="Staging")
    assert db_manager.list_projects("check") [0]["project_id"] == project_id

    device_id = db_manager.save_device_snapshot("emulator-5554", {"model": "test", "ram_total_bytes": 1024})
    saved = db_manager.list_snapshots()
    assert device_id and json.loads(saved[0]["evidence_json"])["ram_total_bytes"] == 1024

    db_manager.save_challenge("interviews", [{"discovery": "retry duplicates"}])
    assert db_manager.load_challenge("interviews")[0]["discovery"] == "retry duplicates"
    assert db_manager.counts()["projects"] == 1


def test_adb_device_states_are_parsed_without_losing_unauthorized_state(monkeypatch):
    monkeypatch.setattr(adb_controller, "run_adb", lambda *args, **kwargs: (
        "List of devices attached\nphone123 device product:x model:Budget_Phone\nemulator-5554 unauthorized\nold offline\n"))
    devices = adb_controller.list_devices()
    assert [(d["serial"], d["state"]) for d in devices] == [
        ("phone123", "device"), ("emulator-5554", "unauthorized"), ("old", "offline")]
    assert devices[0]["model_hint"] == "Budget_Phone"


def test_adb_snapshot_parses_realistic_device_text_and_marks_missing_values(monkeypatch):
    props = {"ro.product.manufacturer": "Example", "ro.product.model": "LowEnd 1",
             "ro.product.name": "product", "ro.hardware": "board", "ro.build.id": "BUILD",
             "ro.build.version.release": "14", "ro.build.version.sdk": "34",
             "ro.product.cpu.abi": "arm64-v8a"}
    def prop(_serial, key):
        return props.get(key)
    def shell(_serial, args):
        if args == ["cat", "/proc/meminfo"]:
            return "MemTotal:       2048000 kB\nMemAvailable:    512000 kB"
        if args == ["cat", "/proc/cpuinfo"]:
            return "processor : 0\nprocessor : 1\nHardware : Example CPU"
        if args == ["df", "-k", "/data"]:
            return "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block 1000 250 750 25% /data"
        if args == ["dumpsys", "battery"]:
            return "level: 73\ntemperature: 315\nAC powered: false"
        raise AssertionError(args)
    monkeypatch.setattr(adb_controller, "_prop", prop)
    monkeypatch.setattr(adb_controller, "_shell", shell)
    result = adb_controller.collect_snapshot("device-serial")
    assert result["ram_total_bytes"] == 2048000 * 1024
    assert result["cpu_cores"] == 2
    assert result["battery_percent"] == 73
    assert result["battery_temperature_c"] == 31.5
    assert result["battery_charging"] is False
    assert result["evidence"]["ram_available_bytes"] == "Snapshot"


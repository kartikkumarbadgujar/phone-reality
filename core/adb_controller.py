"""Safe, read-only Android Debug Bridge helpers used by Device Lab."""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone


class ADBError(RuntimeError):
    pass


def adb_path():
    return shutil.which("adb")


def run_adb(args, serial=None, timeout=8):
    """Run a fixed ADB argument list. No shell is involved and calls are bounded."""
    executable = adb_path()
    if not executable:
        raise ADBError("ADB was not found. Install Android Platform Tools and add adb to PATH.")
    command = [executable]
    if serial:
        command += ["-s", serial]
    command += list(args)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise ADBError(f"ADB command timed out after {timeout} seconds.") from error
    if result.returncode:
        raise ADBError((result.stderr or result.stdout or "ADB command failed").strip()[:500])
    return result.stdout.strip()


def adb_version():
    if not adb_path():
        return None
    return run_adb(["version"], timeout=5).splitlines()[0]


def list_devices():
    output = run_adb(["devices", "-l"], timeout=8)
    devices = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[:2]
        metadata = dict(item.split(":", 1) for item in parts[2:] if ":" in item)
        devices.append({"serial": serial, "state": state, "model_hint": metadata.get("model", "")})
    return devices


def _shell(serial, command):
    return run_adb(["shell", *command], serial=serial)


def _prop(serial, name):
    value = _shell(serial, ["getprop", name])
    return value or None


def _kb_metric(text, pattern):
    match = re.search(pattern, text, re.I | re.M)
    return int(match.group(1)) * 1024 if match else None


def collect_snapshot(serial):
    """Collect available values; unsupported readings stay unavailable rather than guessed."""
    collected = datetime.now(timezone.utc).isoformat(timespec="seconds")
    details = {
        "collected_at": collected,
        "manufacturer": _prop(serial, "ro.product.manufacturer"),
        "model": _prop(serial, "ro.product.model"),
        "product": _prop(serial, "ro.product.name"),
        "hardware": _prop(serial, "ro.hardware"),
        "build_id": _prop(serial, "ro.build.id"),
        "android_version": _prop(serial, "ro.build.version.release"),
        "sdk_version": _prop(serial, "ro.build.version.sdk"),
        "cpu_abi": _prop(serial, "ro.product.cpu.abi"),
    }
    mem = _shell(serial, ["cat", "/proc/meminfo"])
    details["ram_total_bytes"] = _kb_metric(mem, r"^MemTotal:\s+(\d+)")
    details["ram_available_bytes"] = _kb_metric(mem, r"^MemAvailable:\s+(\d+)")
    cpu = _shell(serial, ["cat", "/proc/cpuinfo"])
    details["cpu_cores"] = len(re.findall(r"^processor\s*:", cpu, re.I | re.M)) or None
    details["cpu_model"] = next((m.group(1).strip() for m in re.finditer(r"^(?:model name|Hardware)\s*:\s*(.+)$", cpu, re.I | re.M)), None)
    storage = _shell(serial, ["df", "-k", "/data"])
    lines = storage.splitlines()
    fields = lines[-1].split() if len(lines) > 1 else []
    details["data_storage_total_bytes"] = int(fields[1]) * 1024 if len(fields) >= 4 and fields[1].isdigit() else None
    details["data_storage_available_bytes"] = int(fields[3]) * 1024 if len(fields) >= 4 and fields[3].isdigit() else None
    battery = _shell(serial, ["dumpsys", "battery"])
    level = re.search(r"level:\s*(\d+)", battery)
    temperature = re.search(r"temperature:\s*(\d+)", battery)
    plugged = re.search(r"AC powered:\s*(true|false)|USB powered:\s*(true|false)|Wireless powered:\s*(true|false)", battery, re.I)
    details["battery_percent"] = int(level.group(1)) if level else None
    details["battery_temperature_c"] = int(temperature.group(1)) / 10 if temperature else None
    details["battery_charging"] = (plugged.group(1).lower() == "true") if plugged else None
    details["serial_redacted"] = serial[:2] + "…" + serial[-2:] if len(serial) > 5 else "redacted"
    details["limitations"] = ["RAM is system-wide, not app-specific.",
                              "Storage is the /data filesystem reading, not user-visible free space.",
                              "Battery and thermal availability varies by manufacturer."]
    details["evidence"] = {key: ("Snapshot" if value is not None else "Unavailable")
                           for key, value in details.items() if key not in ("evidence", "limitations")}
    return details

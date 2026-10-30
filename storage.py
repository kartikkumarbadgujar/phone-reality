"""
storage.py - saves and loads PhoneReality's own files (test results and scoring assumptions).

Everything is plain JSON so you can open the files in VS Code and read them.
If the results file is missing or empty we treat it as "no tests yet".
If it is broken (not valid JSON) we move it aside as a backup and start fresh,
and we tell the user, instead of crashing.

Set the environment variable PHONEREALITY_DATA_DIR to use a different folder
(the automated tests do this so they never touch your real results).
"""

import json
import os
from datetime import datetime
from pathlib import Path

# Every saved test record must have these fields; anything else is ignored with a warning.
REQUIRED_KEYS = {
    "test_id", "timestamp", "profile", "profile_label", "operation", "operation_label", "config",
    "evidence_category", "expected_status", "operation_status", "verdict", "duration_s",
    "attempts", "evidence", "actual_outcome", "detections",
}


def data_dir():
    folder = Path(os.environ.get("PHONEREALITY_DATA_DIR", Path(__file__).parent / "data"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def results_path():
    return data_dir() / "results.json"


def assumptions_path():
    return data_dir() / "assumptions.json"


def _write_json(path, value):
    """Write to a temporary file first, then swap it in, so a crash cannot leave half a file."""
    temp = Path(str(path) + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def load_results(path=None):
    """Return (records, warning). warning is None when everything is fine."""
    path = Path(path or results_path())
    if not path.exists():
        return [], None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return [], None
    try:
        records = json.loads(text)
        if not isinstance(records, list):
            raise ValueError("results file must contain a JSON list")
        good = [r for r in records if isinstance(r, dict) and REQUIRED_KEYS <= r.keys()]
        if len(good) < len(records):
            return good, (f"{len(records) - len(good)} record(s) in the results file were incomplete "
                          "and are being ignored.")
        return good, None
    except (json.JSONDecodeError, ValueError) as error:
        backup = path.with_name(f"results.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
        os.replace(path, backup)
        return [], (f"The results file could not be read ({error}). "
                    f"It was moved to {backup.name} and a new, empty file will be used.")


def save_results(records, path=None):
    _write_json(Path(path or results_path()), records)


def append_result(record, path=None):
    records, _ = load_results(path)
    records.append(record)
    save_results(records, path)


def clear_results(path=None):
    save_results([], path)


def load_assumptions(defaults, path=None):
    """Return the saved scoring assumptions, falling back to the defaults."""
    path = Path(path or assumptions_path())
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return json.loads(json.dumps(defaults))  # a fresh copy of the defaults
    merged = json.loads(json.dumps(defaults))
    for section in ("likelihood", "impact_weight"):
        merged[section].update(saved.get(section, {}))
    return merged


def save_assumptions(assumptions, path=None):
    _write_json(Path(path or assumptions_path()), assumptions)

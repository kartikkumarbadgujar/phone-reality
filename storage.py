"""Read and write the JSON files used by the original test engine.

Run records stay as plain JSON so they remain easy to inspect and compatible
with older PhoneReality data. Missing or empty files mean there are no saved
runs. If a file is damaged, keep a timestamped copy and show the user what
happened instead of crashing the app.

Set ``PHONEREALITY_DATA_DIR`` to keep these files somewhere else. The test
suite points it at a temporary folder so it never touches local run history.
"""

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

# Records missing any of these fields are skipped and reported to the user.
REQUIRED_KEYS = {
    "test_id", "timestamp", "profile", "profile_label", "operation", "operation_label", "config",
    "evidence_category", "expected_status", "operation_status", "verdict", "duration_s",
    "attempts", "evidence", "actual_outcome", "detections",
}

# Keep read-and-update operations together when several Streamlit sessions write at once.
_STORAGE_LOCK = threading.RLock()


def data_dir():
    folder = Path(os.environ.get("PHONEREALITY_DATA_DIR", Path(__file__).parent / "data"))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def results_path():
    return data_dir() / "results.json"


def assumptions_path():
    return data_dir() / "assumptions.json"


def _write_json(path, value):
    """Write a complete temporary file before replacing the saved one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with _STORAGE_LOCK:
        try:
            temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def load_results(path=None):
    """Load valid run records and return any warning that should be shown."""
    path = Path(path or results_path())
    with _STORAGE_LOCK:
        if not path.exists():
            return [], None
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            return [], f"The results file could not be read ({error}). Check file permissions and encoding."
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
            backup = path.with_name(f"results.corrupt-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.json")
            try:
                os.replace(path, backup)
            except OSError as move_error:
                return [], f"The results file could not be read ({error}); backup failed ({move_error})."
            return [], (f"The results file could not be read ({error}). "
                        f"It was moved to {backup.name} and a new, empty file will be used.")


def save_results(records, path=None):
    _write_json(Path(path or results_path()), records)


def append_result(record, path=None):
    with _STORAGE_LOCK:
        records, _ = load_results(path)
        records.append(record)
        save_results(records, path)


def clear_results(path=None):
    save_results([], path)


def load_assumptions(defaults, path=None):
    """Load the saved weights, or make a fresh copy of the defaults."""
    path = Path(path or assumptions_path())
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return json.loads(json.dumps(defaults))  # Avoid sharing the caller's nested dictionaries.
    if not isinstance(saved, dict):
        return json.loads(json.dumps(defaults))
    merged = json.loads(json.dumps(defaults))
    for section in ("likelihood", "impact_weight"):
        values = saved.get(section, {})
        if isinstance(values, dict):
            merged[section].update(values)
    return merged


def save_assumptions(assumptions, path=None):
    _write_json(Path(path or assumptions_path()), assumptions)

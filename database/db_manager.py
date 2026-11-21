"""Small SQLite persistence layer for BudgetBench projects and device evidence.

The original PhoneReality JSON run format remains intact for backward compatibility.
This database stores the new product entities and an index of completed runs.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def database_path() -> Path:
    configured = os.environ.get("BUDGETBENCH_DB")
    if configured:
        return Path(configured)
    legacy_data = os.environ.get("PHONEREALITY_DATA_DIR")
    if legacy_data:
        return Path(legacy_data) / "budgetbench.sqlite3"
    return Path(__file__).resolve().parents[1] / "data" / "budgetbench.sqlite3"


@contextmanager
def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
          project_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          target_type TEXT NOT NULL DEFAULT 'REST API', target_url TEXT NOT NULL DEFAULT '',
          package_name TEXT NOT NULL DEFAULT '', environment TEXT NOT NULL DEFAULT 'Development',
          status TEXT NOT NULL DEFAULT 'Active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS devices (
          device_id TEXT PRIMARY KEY, serial_ref TEXT NOT NULL UNIQUE, manufacturer TEXT, model TEXT,
          android_version TEXT, sdk_version TEXT, last_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS device_snapshots (
          snapshot_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
          collected_at TEXT NOT NULL, evidence_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_sessions (
          test_id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(project_id) ON DELETE SET NULL,
          created_at TEXT NOT NULL, profile TEXT NOT NULL, operation TEXT NOT NULL,
          status TEXT NOT NULL, verdict TEXT NOT NULL, evidence_category TEXT NOT NULL,
          duration_s REAL NOT NULL, record_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS challenge_items (
          item_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_created ON test_sessions(created_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_project ON test_sessions(project_id);
        """)


def _dicts(rows):
    return [dict(row) for row in rows]


def list_projects(search=""):
    initialize()
    with connect() as db:
        return _dicts(db.execute("SELECT * FROM projects WHERE name LIKE ? ORDER BY updated_at DESC", (f"%{search}%",)).fetchall())


def save_project(name, description="", target_type="REST API", target_url="", package_name="", environment="Development", project_id=None):
    initialize()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    project_id = project_id or "P-" + uuid.uuid4().hex[:10].upper()
    with connect() as db:
        db.execute("""INSERT INTO projects(project_id,name,description,target_type,target_url,package_name,environment,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,'Active',?,?) ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,
          description=excluded.description,target_type=excluded.target_type,target_url=excluded.target_url,
          package_name=excluded.package_name,environment=excluded.environment,updated_at=excluded.updated_at""",
          (project_id, name.strip(), description.strip(), target_type, target_url.strip(), package_name.strip(), environment, now, now))
    return project_id


def delete_project(project_id):
    initialize()
    with connect() as db:
        db.execute("DELETE FROM projects WHERE project_id=?", (project_id,))


def save_device_snapshot(serial, details):
    initialize()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    device_id = "D-" + uuid.uuid5(uuid.NAMESPACE_URL, serial).hex[:12].upper()
    with connect() as db:
        db.execute("""INSERT INTO devices(device_id,serial_ref,manufacturer,model,android_version,sdk_version,last_seen)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(serial_ref) DO UPDATE SET manufacturer=excluded.manufacturer,
        model=excluded.model,android_version=excluded.android_version,sdk_version=excluded.sdk_version,last_seen=excluded.last_seen""",
        (device_id, serial, details.get("manufacturer"), details.get("model"), details.get("android_version"), details.get("sdk_version"), now))
        db.execute("INSERT INTO device_snapshots VALUES(?,?,?,?)", ("S-" + uuid.uuid4().hex[:12], device_id, now, json.dumps(details)))
    return device_id


def list_snapshots(limit=50):
    initialize()
    with connect() as db:
        return _dicts(db.execute("""SELECT s.snapshot_id,s.collected_at,s.evidence_json,d.model,d.manufacturer,d.android_version
          FROM device_snapshots s JOIN devices d USING(device_id) ORDER BY s.collected_at DESC LIMIT ?""", (limit,)).fetchall())


def index_run(record, project_id=None):
    initialize()
    with connect() as db:
        db.execute("""INSERT INTO test_sessions VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(test_id) DO UPDATE SET project_id=COALESCE(excluded.project_id,test_sessions.project_id),
          created_at=excluded.created_at,profile=excluded.profile,operation=excluded.operation,status=excluded.status,
          verdict=excluded.verdict,evidence_category=excluded.evidence_category,duration_s=excluded.duration_s,
          record_json=excluded.record_json""",
          (record["test_id"], project_id, record["timestamp"], record["profile"], record["operation"],
           record["operation_status"], record["verdict"], record["evidence_category"], record["duration_s"], json.dumps(record)))


def list_runs(project_id=None, limit=250):
    initialize()
    with connect() as db:
        if project_id:
            rows = db.execute("SELECT * FROM test_sessions WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM test_sessions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return _dicts(rows)


def save_challenge(key, value):
    initialize()
    with connect() as db:
        db.execute("INSERT INTO challenge_items VALUES(?,?,?) ON CONFLICT(item_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                   (key, json.dumps(value), datetime.now(timezone.utc).isoformat(timespec="seconds")))


def load_challenge(key, default=None):
    initialize()
    with connect() as db:
        row = db.execute("SELECT value_json FROM challenge_items WHERE item_key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def counts():
    initialize()
    with connect() as db:
        return {
            "projects": db.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "devices": db.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
            "device_snapshots": db.execute("SELECT COUNT(*) FROM device_snapshots").fetchone()[0],
            "test_sessions": db.execute("SELECT COUNT(*) FROM test_sessions").fetchone()[0],
        }

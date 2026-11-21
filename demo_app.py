"""Local HTTP service used by BudgetBench's test lab.

It stands in for a simple app backend: return screen data, accept a file and
show which uploads were saved. Tests can ask it to delay a reply or return a
few HTTP 503 responses. For uploads, the file is saved before the optional
delay. That lets us reproduce the case where the client times out but the
server has already finished the work.

These are controlled faults in this service; they do not change a phone's
network. Start it with ``python demo_app.py``.
"""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

MAX_DELAY_SECONDS = 30
MAX_FAIL_COUNT = 10
MAX_ACTIVE_FAULT_KEYS = 1024
MAX_REQUEST_BYTES = 6 * 1024 * 1024
MAX_STORED_UPLOADS = 1000
MAX_STORED_BYTES = 256 * 1024 * 1024
DEFAULT_PORT = 5050
UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def create_app(data_dir):
    """Create the service and keep its uploads under ``data_dir``."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

    data_dir = Path(data_dir)
    files_dir = data_dir / "server_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    upload_log_file = data_dir / "server_uploads.json"

    lock = threading.Lock()  # Only one request at a time may update the receipt log.
    fault_counters = {}  # Number of attempts seen for each fault key.

    def read_upload_log():
        if not upload_log_file.exists():
            return []
        try:
            records = json.loads(upload_log_file.read_text(encoding="utf-8") or "[]")
        except (json.JSONDecodeError, OSError, UnicodeError) as error:
            raise RuntimeError("The demo receipt log is unreadable; preserve it and check its permissions.") from error
        if not isinstance(records, list) or any(
            not isinstance(record, dict)
            or not isinstance(record.get("upload_id"), str)
            or not isinstance(record.get("sha256"), str)
            or not isinstance(record.get("size_bytes"), int)
            or not isinstance(record.get("receipt_id"), str)
            for record in records
        ):
            raise RuntimeError("The demo receipt log has an invalid structure; preserve it before repair.")
        return records

    def write_upload_log(records):
        # Write beside the log, then replace it so a partial write cannot break saved history.
        temp_path = upload_log_file.with_name(f"{upload_log_file.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
            os.replace(temp_path, upload_log_file)
        finally:
            temp_path.unlink(missing_ok=True)

    @app.errorhandler(RuntimeError)
    def receipt_log_error(error):
        return jsonify(error=str(error)), 500

    @app.before_request
    def allow_local_client_only():
        """Only accept loopback traffic and requests sent directly by the test harness."""
        try:
            peer = ipaddress.ip_address(request.remote_addr or "")
            host = urlsplit(f"//{request.host}").hostname or ""
            host_is_local = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            return jsonify(error="This demo service accepts loopback connections only."), 403
        if not peer.is_loopback or not host_is_local:
            return jsonify(error="This demo service accepts loopback connections only."), 403
        # The harness does not send an Origin header. Reject browser-origin requests
        # so another site cannot use this local service as a cross-site target.
        if request.headers.get("Origin") or request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="Cross-site requests are not allowed."), 403

        if request.path in ("/api/data", "/api/upload"):
            raw_delay = request.args.get("delay", "0")
            raw_fail_count = request.args.get("fail_count", "0")
            try:
                delay = float(raw_delay)
                fail_count = int(raw_fail_count)
            except (TypeError, ValueError):
                return jsonify(error="Invalid fault-injection settings."), 400
            if not math.isfinite(delay) or not 0 <= delay <= MAX_DELAY_SECONDS:
                return jsonify(error=f"delay must be between 0 and {MAX_DELAY_SECONDS} seconds."), 400
            if not 0 <= fail_count <= MAX_FAIL_COUNT:
                return jsonify(error=f"fail_count must be between 0 and {MAX_FAIL_COUNT}."), 400
            if fail_count:
                fault_key = request.args.get("fault_key", "")
                if not UPLOAD_ID_RE.fullmatch(fault_key):
                    return jsonify(error="fault_key must be 1-128 letters, digits, dots, underscores, or hyphens."), 400
            if request.path == "/api/upload" and request.args.get("idempotent", "0") not in ("0", "1"):
                return jsonify(error="idempotent must be 0 or 1."), 400
        return None

    def injected_failure():
        """Apply the configured HTTP 503 fault, or let the request continue."""
        fail_count = request.args.get("fail_count", 0, type=int)
        if fail_count <= 0:
            return None
        key = request.args.get("fault_key", "")
        with lock:
            if key not in fault_counters and len(fault_counters) >= MAX_ACTIVE_FAULT_KEYS:
                return jsonify(error="Too many active fault-injection scenarios."), 429
            seen_before = fault_counters.get(key, 0)
            if seen_before < fail_count:
                fault_counters[key] = seen_before + 1
            else:
                fault_counters.pop(key, None)
        if seen_before < fail_count:
            body = {
                "error": "Service temporarily unavailable",
                "controlled_fault_injection": True,
                "failure_number": seen_before + 1,
                "fail_count": fail_count,
            }
            return jsonify(body), 503
        return None

    def injected_delay():
        """Wait for the requested, bounded delay and return the actual wait."""
        delay = request.args.get("delay", 0.0, type=float)
        delay = min(max(delay, 0.0), MAX_DELAY_SECONDS)
        if delay > 0:
            time.sleep(delay)
        return delay

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", service="PhoneReality demo app",
                       server_time=datetime.now().isoformat(timespec="seconds"))

    @app.get("/api/data")
    def load_data():
        failure = injected_failure()
        if failure:
            return failure
        delay = injected_delay()
        items = [{"id": i, "name": f"Item {i}", "price": 10 * i} for i in range(1, 21)]
        return jsonify(items=items, injected_delay_s=delay)

    @app.post("/api/upload")
    def upload():
        failure = injected_failure()
        if failure:
            return failure  # A 503 here means this request never reaches the save step.

        file = request.files.get("file")
        upload_id = request.form.get("upload_id", "")
        attempt = request.form.get("attempt", "")
        if file is None or not UPLOAD_ID_RE.fullmatch(upload_id) or len(attempt) > 20:
            return jsonify(error="Send a 'file' and an 'upload_id'"), 400

        content = file.read()
        sha256 = hashlib.sha256(content).hexdigest()
        idempotent = request.args.get("idempotent", 0, type=int) == 1

        # Save the file first. The delayed response below makes the result ambiguous.
        with lock:
            log = read_upload_log()
            already_saved = [r for r in log if r["upload_id"] == upload_id]
            if idempotent and already_saved:
                if already_saved[0]["sha256"] != sha256 or already_saved[0]["size_bytes"] != len(content):
                    return jsonify(error="This upload_id was already used for different file content."), 409
                stored_now = False
                receipt = already_saved[0]
            else:
                stored_bytes = sum(item.get("size_bytes", 0) for item in log)
                if len(log) >= MAX_STORED_UPLOADS or stored_bytes + len(content) > MAX_STORED_BYTES:
                    return jsonify(error="Demo upload storage is full; clear demo data before retrying."), 507
                stored_now = True
                receipt_id = "R-" + uuid.uuid4().hex[:8]
                stored_file = files_dir / f"{receipt_id}.bin"
                stored_file.write_bytes(content)
                receipt = {
                    "receipt_id": receipt_id,
                    "upload_id": upload_id,
                    "attempt": attempt,
                    "size_bytes": len(content),
                    "sha256": sha256,
                    "saved_at": datetime.now().isoformat(timespec="milliseconds"),
                }
                log.append(receipt)
                try:
                    write_upload_log(log)
                except OSError:
                    stored_file.unlink(missing_ok=True)
                    raise

        # The client may time out during this wait even though the file is saved.
        delay = injected_delay()
        return jsonify(
            status="stored" if stored_now else "already_stored",
            receipt_id=receipt["receipt_id"],
            upload_id=upload_id,
            size_bytes=len(content),
            sha256=sha256,
            injected_ack_delay_s=delay,
        )

    @app.get("/api/uploads/<upload_id>")
    def upload_status(upload_id):
        if not UPLOAD_ID_RE.fullmatch(upload_id):
            return jsonify(error="Invalid upload_id."), 400
        with lock:
            receipts = [r for r in read_upload_log() if r["upload_id"] == upload_id]
        return jsonify(upload_id=upload_id, receipt_count=len(receipts), receipts=receipts)

    return app


def start_in_background(data_dir, host="127.0.0.1", port=DEFAULT_PORT):
    """Run the service in a background thread in this Python process.

    Used by the automated tests and by the online (Streamlit Cloud) version, where only one
    program can run. Requests to it are still real HTTP requests over 127.0.0.1.
    port=0 means "pick any free port". Returns (server, base_url).
    """
    from werkzeug.serving import make_server

    server = make_server(host, port, create_app(data_dir), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_port}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the BudgetBench demo service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    demo = create_app(Path(__file__).parent / "data")
    print(f"BudgetBench demo service running at http://{args.host}:{args.port}")
    print("Leave this window open. Press Ctrl+C to stop.")
    demo.run(host=args.host, port=args.port, threaded=True, debug=False)

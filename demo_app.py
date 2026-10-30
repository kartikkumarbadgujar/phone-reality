"""
demo_app.py - the small "app backend" that PhoneReality tests.

Think of this as the server behind a simple mobile app. It has three features:
  GET  /api/health              -> "is the server alive?" (never has faults)
  GET  /api/data                -> loads the data for a screen (a list of items)
  POST /api/upload              -> uploads a file (like a photo or a form)
  GET  /api/uploads/<upload_id> -> lets the tester check what the server REALLY saved

The tester can switch on CONTROLLED FAULTS using query parameters:
  delay=3          wait 3 seconds before replying.
                   For uploads the wait happens AFTER the file is saved
                   (a "delayed acknowledgement").
  fail_count=2     the first 2 requests with the same fault_key get HTTP 503.
  fault_key=abc    any text; groups the requests that belong to one test.
  idempotent=1     (upload only) the FIXED behaviour: if the same upload_id
                   arrives twice, store it only once.

Nothing here touches the real network. These are deliberate faults inside the
app, so the client sees a genuine HTTP outcome (real timeout, real 503).

Run it with:   python demo_app.py
"""

import argparse
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

MAX_DELAY_SECONDS = 30
DEFAULT_PORT = 5050


def create_app(data_dir):
    """Build the Flask app. data_dir is where uploads and the upload log are saved."""
    app = Flask(__name__)

    data_dir = Path(data_dir)
    files_dir = data_dir / "server_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    upload_log_file = data_dir / "server_uploads.json"

    lock = threading.Lock()   # stops two requests from writing the log at the same time
    fault_counters = {}       # fault_key -> how many requests we have seen for it

    def read_upload_log():
        if not upload_log_file.exists():
            return []
        try:
            return json.loads(upload_log_file.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            return []

    def write_upload_log(records):
        upload_log_file.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def injected_failure():
        """Return an HTTP 503 response if this request should fail, otherwise None."""
        fail_count = request.args.get("fail_count", 0, type=int)
        if fail_count <= 0:
            return None
        key = request.args.get("fault_key", "")
        with lock:
            seen_before = fault_counters.get(key, 0)
            fault_counters[key] = seen_before + 1
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
        """Sleep for the requested delay (capped) and return how long we slept."""
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
            return failure  # a 503 happens BEFORE anything is saved

        file = request.files.get("file")
        upload_id = request.form.get("upload_id", "")
        if file is None or not upload_id:
            return jsonify(error="Send a 'file' and an 'upload_id'"), 400

        content = file.read()
        sha256 = hashlib.sha256(content).hexdigest()
        idempotent = request.args.get("idempotent", 0, type=int) == 1

        # Step 1: save the upload (this really writes the file to disk).
        with lock:
            log = read_upload_log()
            already_saved = [r for r in log if r["upload_id"] == upload_id]
            if idempotent and already_saved:
                stored_now = False
                receipt = already_saved[0]
            else:
                stored_now = True
                receipt_id = "R-" + uuid.uuid4().hex[:8]
                (files_dir / f"{receipt_id}.bin").write_bytes(content)
                receipt = {
                    "receipt_id": receipt_id,
                    "upload_id": upload_id,
                    "attempt": request.form.get("attempt", ""),
                    "size_bytes": len(content),
                    "sha256": sha256,
                    "saved_at": datetime.now().isoformat(timespec="milliseconds"),
                }
                log.append(receipt)
                write_upload_log(log)

        # Step 2: only now apply the delay. The file is ALREADY saved, but the
        # client does not know that until this reply arrives.
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
        with lock:
            receipts = [r for r in read_upload_log() if r["upload_id"] == upload_id]
        return jsonify(upload_id=upload_id, receipt_count=len(receipts), receipts=receipts)

    return app


def start_in_background(data_dir, host="127.0.0.1", port=DEFAULT_PORT):
    """Run the demo app in a background thread of the CURRENT program.

    Used by the automated tests and by the online (Streamlit Cloud) version, where only one
    program can run. Requests to it are still real HTTP requests over 127.0.0.1.
    port=0 means "pick any free port". Returns (server, base_url).
    """
    from werkzeug.serving import make_server

    server = make_server(host, port, create_app(data_dir), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://{host}:{server.server_port}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PhoneReality demo backend")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    demo = create_app(Path(__file__).parent / "data")
    print(f"PhoneReality demo app running at http://{args.host}:{args.port}")
    print("Leave this window open. Press Ctrl+C to stop.")
    demo.run(host=args.host, port=args.port, threaded=True, debug=False)

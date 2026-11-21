"""
tester.py - runs REAL HTTP tests against the demo app and builds a test record.

One test = one operation (load data or upload a file) under one condition profile.
The profile only fills in default settings (timeout, delay, ...). The settings that
were actually used are saved in the record's "config", so a test can be repeated exactly.

Each record keeps two different answers apart:
  operation_status - what the app experienced: SUCCESS, TIMEOUT, HTTP_ERROR, LOST_SIMULATED
  verdict          - did PhoneReality reproduce the condition it predicted?
                     PASS / FAIL / INCONCLUSIVE / ERROR
So "operation_status = TIMEOUT, verdict = PASS" means: we caused a timeout on purpose
and the tester observed exactly that.

You can also run a test from the terminal (handy for reproducing a finding):
    python tester.py --operation upload --profile slow_network
"""

import argparse
import hashlib
import ipaddress
import json
import math
import random
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

import requests

import detectors
import storage

DEFAULT_BASE_URL = "http://127.0.0.1:5050"

# Operation statuses (what the client experienced)
SUCCESS = "SUCCESS"
TIMEOUT = "TIMEOUT"
HTTP_ERROR = "HTTP_ERROR"
CONNECTION_ERROR = "CONNECTION_ERROR"
LOST_SIMULATED = "LOST_SIMULATED"

# Evidence categories
GENUINE = "GENUINE"
FAULT_INJECTION = "FAULT_INJECTION"
SIMULATED = "SIMULATED"

CATEGORY_LABELS = {
    GENUINE: "Genuine local measurement",
    FAULT_INJECTION: "Controlled fault injection (genuine HTTP outcome)",
    SIMULATED: "SIMULATED",
}

OPERATIONS = {
    "load_data": {
        "label": "Load screen data (GET /api/data)",
        "method": "GET",
        "path": "/api/data",
        "user_expectation": "The screen data loads, or the app shows a clear error and lets the user try again.",
    },
    "upload": {
        "label": "Upload a file (POST /api/upload)",
        "method": "POST",
        "path": "/api/upload",
        "user_expectation": "The upload is saved exactly once and the user is told correctly whether it worked.",
    },
}

PROFILES = {
    "normal": {
        "label": "Normal",
        "short": "Normal",
        "description": "No faults. Establishes the baseline: real requests to the local demo app.",
        "what_happens": "Standard HTTP request / upload with no injected fault.",
        "evidence_category": GENUINE,
        "defaults": {"timeout": 3.0, "server_delay": 0.0, "fail_count": 0, "retries": 0, "loss_rate": 0.0},
        "expected_behaviour": "Request succeeds quickly; upload stored once.",
    },
    "slow_network": {
        "label": "Slow Network",
        "short": "Slow network",
        "description": "The server waits before replying, longer than the client is willing to wait. "
                       "For uploads, the file is saved FIRST and only the reply is delayed.",
        "what_happens": "Injected server delay (3 s) larger than the client timeout (1 s).",
        "evidence_category": FAULT_INJECTION,
        "defaults": {"timeout": 1.0, "server_delay": 3.0, "fail_count": 0, "retries": 0, "loss_rate": 0.0},
        "expected_behaviour": "Client raises a real requests Timeout; for uploads the server may still have saved the file.",
    },
    "intermittent": {
        "label": "Intermittent Connectivity",
        "short": "Intermittent",
        "description": "The server deliberately answers HTTP 503 for the first few requests of the test, "
                       "then recovers. Shows whether retries are enough to recover.",
        "what_happens": "First 2 requests get HTTP 503; client retries once.",
        "evidence_category": FAULT_INJECTION,
        "defaults": {"timeout": 3.0, "server_delay": 0.0, "fail_count": 2, "retries": 1, "loss_rate": 0.0},
        "expected_behaviour": "Both attempts receive 503, so the operation does not recover (raise retries to 2 to see recovery).",
    },
    "packet_loss": {
        "label": "Packet Loss (SIMULATED)",
        "short": "Packet loss (SIM)",
        "description": "SIMULATION: before each attempt, a seeded random number decides whether the request "
                       "is 'lost'. Lost requests are never sent. Requests that are not lost are real.",
        "what_happens": "Each attempt is dropped with probability loss_rate (seeded, so repeatable).",
        "evidence_category": SIMULATED,
        # seed 4 gives random numbers 0.236 and 0.103, both below 0.5, so both attempts are dropped
        "defaults": {"timeout": 3.0, "server_delay": 0.0, "fail_count": 0, "retries": 1, "loss_rate": 0.5,
                     "seed": 4},
        "expected_behaviour": "Depends on the seed; the default seed 4 loses both attempts, seed 1 loses only the first.",
    },
}

# Shown in the condition matrix only. There are deliberately no buttons for these.
FUTURE_PROFILES = {
    "Process Interruption": "Needs a real Android app or emulator to kill the process and check saved state.",
    "Resource Pressure (memory / storage)": "Needs device-level instrumentation (adb, emulator) to measure honestly.",
    "Thermal / Battery Restrictions": "Needs a physical device or emulator; cannot be measured from a laptop HTTP client.",
}

DEFAULT_SEED = 42  # used for the upload file content (and loss, unless a profile sets its own seed)


# ---------------------------------------------------------------- configuration

def build_config(operation, profile, base_url=DEFAULT_BASE_URL, **overrides):
    """Start from the profile's defaults, then apply any settings the user changed."""
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile '{profile}'. Choose from: {', '.join(PROFILES)}")
    config = {"operation": operation, "profile": profile, "base_url": base_url,
              "seed": DEFAULT_SEED, "upload_size_kb": 64, "idempotent_server": False}
    config.update(PROFILES[profile]["defaults"])
    config.update({key: value for key, value in overrides.items() if value is not None})
    validate_config(config)
    return config


def validate_config(config):
    """Raise ValueError with a clear message if a setting is missing or out of range."""
    required = ["operation", "profile", "base_url", "timeout", "server_delay", "fail_count",
                "retries", "loss_rate", "seed", "upload_size_kb", "idempotent_server"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Test configuration is missing: {', '.join(missing)}")
    if config["operation"] not in OPERATIONS:
        raise ValueError(f"Unknown operation '{config['operation']}'")
    if config["profile"] not in PROFILES:
        raise ValueError(f"Unknown profile '{config['profile']}'")
    base_url = config["base_url"]
    if not isinstance(base_url, str) or len(base_url) > 2048:
        raise ValueError("base_url must be a valid local demo URL (maximum 2048 characters).")
    parsed = None
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        local_host = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        _ = parsed.port  # Validate malformed ports before any network request.
    except ValueError:
        local_host = False
    if (parsed is None or parsed.scheme != "http" or not local_host or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Only an HTTP loopback URL for the local demo server is allowed.")
    if not isinstance(config["idempotent_server"], bool):
        raise ValueError("idempotent_server must be true or false.")
    integer_checks = ("fail_count", "retries", "seed", "upload_size_kb")
    for key in integer_checks:
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            raise ValueError(f"{key} must be an integer.")
    for key in ("timeout", "server_delay", "loss_rate"):
        if isinstance(config[key], bool) or not isinstance(config[key], (int, float)):
            raise ValueError(f"{key} must be a number.")
        if not math.isfinite(config[key]):
            raise ValueError(f"{key} must be finite.")
    checks = [
        ("timeout", 0.2, 60), ("server_delay", 0, 30), ("fail_count", 0, 10),
        ("retries", 0, 5), ("loss_rate", 0, 1), ("upload_size_kb", 1, 5120),
    ]
    for key, low, high in checks:
        if not low <= config[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}, got {config[key]}")


def evidence_category(config):
    """Decide the evidence category from the settings actually used, not from the profile name."""
    if config["loss_rate"] > 0:
        return SIMULATED
    if config["server_delay"] > 0 or config["fail_count"] > 0:
        return FAULT_INJECTION
    return GENUINE


def simulated_drops(config):
    """For each attempt, True if the packet-loss SIMULATION drops it. Same seed -> same answer."""
    attempts = config["retries"] + 1
    if config["loss_rate"] <= 0:
        return [False] * attempts
    rng = random.Random(config["seed"])
    return [rng.random() < config["loss_rate"] for _ in range(attempts)]


def _http_client():
    """Use a fresh session without environment proxies for loopback-only requests."""
    client = requests.Session()
    client.trust_env = False
    return client


def _read_limited_response(response, limit):
    """Read only a small response excerpt so a local wrong-target cannot exhaust memory."""
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=1024):
        if not chunk:
            continue
        remaining = limit - total
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if total >= limit:
            break
    return b"".join(chunks)


def predict_status(config):
    """Work out, from the settings alone, what the operation status SHOULD be.

    Returns (status, reason). The status is "UNPREDICTABLE" when the delay and the
    timeout are so close that normal timing noise could decide the result.
    """
    delay, timeout = config["server_delay"], config["timeout"]
    if delay > 0 and abs(delay - timeout) < 0.3:
        return "UNPREDICTABLE", (f"Delay {delay} s is within 0.3 s of the timeout {timeout} s, "
                                 "so the result depends on timing noise. Move them further apart.")
    drops = simulated_drops(config)
    sent = 0
    reasons = []
    for number, dropped in enumerate(drops, start=1):
        if dropped:
            reasons.append(f"attempt {number}: dropped by simulation")
            last = LOST_SIMULATED
            continue
        sent += 1
        if sent <= config["fail_count"]:
            reasons.append(f"attempt {number}: server injects HTTP 503 ({sent} of {config['fail_count']})")
            last = HTTP_ERROR
        elif delay >= timeout:
            reasons.append(f"attempt {number}: delay {delay} s >= timeout {timeout} s -> timeout")
            last = TIMEOUT
        else:
            reasons.append(f"attempt {number}: delay {delay} s < timeout {timeout} s -> success")
            return SUCCESS, "; ".join(reasons)
    return last, "; ".join(reasons)


# ---------------------------------------------------------------- running a test

def check_server(base_url, timeout=3.0):
    """Return (True, message) if the demo app answers /api/health, else (False, help text)."""
    try:
        with _http_client() as client:
            with client.get(base_url.rstrip("/") + "/api/health", timeout=timeout,
                            allow_redirects=False, stream=True,
                            headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                status_code = response.status_code
                body_bytes = _read_limited_response(response, 4097) if status_code == 200 else b""
        if status_code == 200 and len(body_bytes) <= 4096:
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except (ValueError, UnicodeError):
                body = {}
            if body.get("service") == "PhoneReality demo app" and body.get("status") == "ok":
                return True, f"Demo app is running at {base_url}"
        return False, f"{base_url} is not the expected demo app (HTTP {status_code})."
    except requests.exceptions.ReadTimeout:
        return False, (f"The demo app at {base_url} accepted the connection but did not answer within "
                       f"{timeout} s. The laptop may be busy - wait a moment and try again.")
    except requests.exceptions.RequestException as error:
        return False, (f"Cannot reach the demo app at {base_url} ({type(error).__name__}). "
                       "Start it in a separate terminal with:  python demo_app.py")


def make_payload(config):
    """Deterministic file content: the same seed and size always give the same bytes and hash."""
    return random.Random(config["seed"]).randbytes(config["upload_size_kb"] * 1024)


def send_request(config, fault_key, upload_id, payload, attempt_number):
    """Send ONE real HTTP request and describe what happened."""
    operation = OPERATIONS[config["operation"]]
    url = config["base_url"].rstrip("/") + operation["path"]
    params = {"delay": config["server_delay"], "fail_count": config["fail_count"], "fault_key": fault_key}
    attempt = {"attempt": attempt_number, "url": url, "status": None, "http_status": None,
               "elapsed_s": None, "error_type": None, "error_message": None, "response_excerpt": None}
    started = time.perf_counter()
    try:
        if config["operation"] == "upload":
            params["idempotent"] = 1 if config["idempotent_server"] else 0
            with _http_client() as client:
                with client.post(
                    url, params=params, timeout=config["timeout"], allow_redirects=False,
                    stream=True,
                    files={"file": ("photo.bin", payload, "application/octet-stream")},
                    data={"upload_id": upload_id, "attempt": attempt_number},
                ) as response:
                    attempt["http_status"] = response.status_code
                    attempt["response_excerpt"] = _read_limited_response(response, 4096).decode("utf-8", "replace")
        else:
            with _http_client() as client:
                with client.get(url, params=params, timeout=config["timeout"], allow_redirects=False,
                                stream=True) as response:
                    attempt["http_status"] = response.status_code
                    attempt["response_excerpt"] = _read_limited_response(response, 4096).decode("utf-8", "replace")
        attempt["status"] = SUCCESS if 200 <= attempt["http_status"] < 300 else HTTP_ERROR
    except requests.exceptions.ReadTimeout as error:
        # Connected, request sent, but no reply in time: the timeout we are testing for.
        attempt["status"] = TIMEOUT
        attempt["error_type"] = type(error).__name__
        attempt["error_message"] = str(error)[:300]
    except requests.exceptions.ConnectionError as error:
        # Includes ConnectTimeout: we never reached the server (on Windows a stopped
        # server often shows up as a ~2 s connect timeout). A setup problem, not a finding.
        attempt["status"] = CONNECTION_ERROR
        attempt["error_type"] = type(error).__name__
        attempt["error_message"] = str(error)[:300]
    except requests.exceptions.RequestException as error:
        attempt["status"] = CONNECTION_ERROR
        attempt["error_type"] = type(error).__name__
        attempt["error_message"] = str(error)[:300]
    attempt["elapsed_s"] = round(time.perf_counter() - started, 3)
    return attempt


def check_upload_on_server(base_url, upload_id):
    """Ask the server what it REALLY saved for this upload_id (independent of the client's view)."""
    try:
        with _http_client() as client:
            with client.get(f"{base_url.rstrip('/')}/api/uploads/{upload_id}", timeout=5,
                            allow_redirects=False, stream=True,
                            headers={"Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                status_code = response.status_code
                body_bytes = _read_limited_response(response, 256 * 1024 + 1) if status_code == 200 else b""
        if status_code != 200 or len(body_bytes) > 256 * 1024:
            return {"checked": False, "error": f"Unexpected upload-status response HTTP {status_code} or oversized body."}
        body = json.loads(body_bytes.decode("utf-8"))
        return {"checked": True, "receipt_count": body["receipt_count"],
                "server_sha256": [r["sha256"] for r in body["receipts"]],
                "receipt_ids": [r["receipt_id"] for r in body["receipts"]]}
    except (requests.exceptions.RequestException, ValueError, KeyError) as error:
        return {"checked": False, "error": f"{type(error).__name__}: {error}"}


def describe_outcome(record):
    """One readable sentence about what actually happened."""
    last = record["attempts"][-1]
    status = record["operation_status"]
    tries = len(record["attempts"])
    if status == SUCCESS:
        text = f"Completed with HTTP {last['http_status']} in {last['elapsed_s']:.2f} s"
        text += f" on attempt {tries}." if tries > 1 else "."
    elif status == TIMEOUT:
        text = (f"Client gave up after {last['elapsed_s']:.2f} s ({last['error_type']}): "
                f"no reply within the {record['config']['timeout']} s timeout.")
    elif status == HTTP_ERROR:
        text = f"Server replied HTTP {last['http_status']}."
    elif status == LOST_SIMULATED:
        text = "The last attempt was dropped by the packet-loss SIMULATION and never reached the server."
    else:
        text = f"Could not connect to the demo app: {last['error_type']}."
    if tries > 1 and status != SUCCESS:
        text += f" All {tries} attempts failed."
    check = record["evidence"].get("server_check")
    if check and check.get("checked"):
        text += f" Server check: {check['receipt_count']} saved copy(ies) of this upload."
    return text


def run_test(config, save=True):
    """Run one complete test and return its record.

    The record is appended to data/results.json when save=True. A test that could not
    reach the demo app at all (verdict ERROR) is NOT saved: that is a setup problem,
    not a finding about the app.
    """
    config = dict(config)
    validate_config(config)
    test_id = f"T-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    is_upload = config["operation"] == "upload"
    upload_id = "U-" + test_id[2:] if is_upload else None
    payload = make_payload(config) if is_upload else None
    expected_status, expected_reason = predict_status(config)
    drops = simulated_drops(config)

    started = time.perf_counter()
    attempts = []
    for number, dropped in enumerate(drops, start=1):
        if dropped:
            attempts.append({"attempt": number, "status": LOST_SIMULATED, "http_status": None,
                             "elapsed_s": 0.0, "error_type": "SimulatedPacketLoss",
                             "error_message": "SIMULATED: request dropped before sending; it never reached the server.",
                             "url": None, "response_excerpt": None})
            continue
        attempt = send_request(config, test_id, upload_id, payload, number)
        attempts.append(attempt)
        if attempt["status"] in (SUCCESS, CONNECTION_ERROR):
            break
    duration = round(time.perf_counter() - started, 3)

    succeeded = any(a["status"] == SUCCESS for a in attempts)
    operation_status = SUCCESS if succeeded else attempts[-1]["status"]

    evidence = {
        "attempts_made": len(attempts),
        "requests_sent_to_server": sum(1 for a in attempts if a["status"] != LOST_SIMULATED),
        "simulated_drops": sum(1 for a in attempts if a["status"] == LOST_SIMULATED),
        "final_http_status": attempts[-1]["http_status"],
    }
    if is_upload:
        evidence["upload_id"] = upload_id
        evidence["client_size_bytes"] = len(payload)
        evidence["client_sha256"] = hashlib.sha256(payload).hexdigest()
        evidence["server_check"] = check_upload_on_server(config["base_url"], upload_id)

    if operation_status == CONNECTION_ERROR:
        verdict = "ERROR"
    elif expected_status == "UNPREDICTABLE":
        verdict = "INCONCLUSIVE"
    elif operation_status == expected_status:
        verdict = "PASS"
    else:
        verdict = "FAIL"

    failing = next((a for a in reversed(attempts) if a["status"] != SUCCESS), None)
    record = {
        "test_id": test_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "profile": config["profile"],
        "profile_label": PROFILES[config["profile"]]["label"],
        "operation": config["operation"],
        "operation_label": OPERATIONS[config["operation"]]["label"],
        "config": config,
        "evidence_category": evidence_category(config),
        "user_expectation": OPERATIONS[config["operation"]]["user_expectation"],
        "expected_status": expected_status,
        "expected_outcome": expected_reason,
        "operation_status": operation_status,
        "verdict": verdict,
        "duration_s": duration,
        "error": ({"type": failing["error_type"], "message": failing["error_message"],
                   "http_status": failing["http_status"]} if failing and not succeeded else None),
        "attempts": attempts,
        "evidence": evidence,
    }
    record["actual_outcome"] = describe_outcome(record)
    record["detections"] = detectors.detect(record) if verdict != "ERROR" else []
    record["finding_ids"] = [d["finding_id"] for d in record["detections"]]

    if save and verdict != "ERROR":
        storage.append_result(record)
    return record


# ---------------------------------------------------------------- command line

def make_parser():
    parser = argparse.ArgumentParser(description="Run one PhoneReality test from the terminal.")
    parser.add_argument("--operation", choices=list(OPERATIONS), required=True)
    parser.add_argument("--profile", choices=list(PROFILES), required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--delay", type=float, dest="server_delay")
    parser.add_argument("--fail-count", type=int)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--loss-rate", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--upload-size-kb", type=int)
    parser.add_argument("--idempotent-server", action="store_true", default=None)
    parser.add_argument("--no-save", action="store_true", help="do not add the result to data/results.json")
    return parser


def config_from_args(args):
    return build_config(args.operation, args.profile, args.base_url,
                        timeout=args.timeout, server_delay=args.server_delay,
                        fail_count=args.fail_count, retries=args.retries,
                        loss_rate=args.loss_rate, seed=args.seed,
                        upload_size_kb=args.upload_size_kb,
                        idempotent_server=args.idempotent_server)


if __name__ == "__main__":
    cli_args = make_parser().parse_args()
    test_config = config_from_args(cli_args)
    ok, message = check_server(test_config["base_url"])
    if not ok:
        raise SystemExit(message)
    result = run_test(test_config, save=not cli_args.no_save)
    print(f"Test {result['test_id']}  [{result['evidence_category']}]")
    print(f"  expected status : {result['expected_status']}")
    print(f"  actual status   : {result['operation_status']}")
    print(f"  verdict         : {result['verdict']}")
    print(f"  what happened   : {result['actual_outcome']}")
    for detection in result["detections"]:
        print(f"  FINDING {detection['finding_id']}: {detection['title']} - {detection['explanation']}")
    if not result["detections"]:
        print("  no findings")
    print("  saved to data/results.json" if not cli_args.no_save else "  (not saved)")

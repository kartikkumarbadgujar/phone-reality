"""
detectors.py - decides, with simple fixed rules, whether a test record shows a failure.

There is no AI and no guessing here. Every detector is one "if" statement you can read
in detect() below. The same record always produces the same detections.

Two steps:
  detect(record)            -> list of detections for ONE test (called by tester.py)
  build_findings(records)   -> groups detections from ALL tests into findings,
                               adds evidence, reproduction steps and a priority score.

A finding is identified by (detector, operation, profile, evidence category). If you run
the same failing test five times you get ONE finding seen five times, not five findings.
"""

import hashlib

import scoring

DETECTORS = {
    "DUPLICATE_UPLOAD": {
        "title": "Retried upload was saved more than once",
        "severity": 5,
        "rule": "upload AND server receipt_count > 1 for a single upload_id",
        "description": "The client retried an upload whose first attempt had already been saved. "
                       "The server stored every attempt, so one user action created several records.",
        "expected_behaviour": "One user action creates exactly one record on the server.",
        "user_impact": "Duplicate posts, orders or payments; the user may be charged or counted twice.",
        "suggested_action": "Send an idempotency key (upload_id) with every upload and make the server "
                            "store each key only once. Tick 'Server de-duplicates' in Run Tests to see the fix.",
    },
    "AMBIGUOUS_UPLOAD": {
        "title": "Client reported failure but the server saved the upload",
        "severity": 4,
        "rule": "upload AND client did not get a success reply AND server receipt_count >= 1",
        "description": "The server saved the file and then replied too slowly. The client timed out and "
                       "treats the upload as failed, although it actually succeeded.",
        "expected_behaviour": "The app tells the user the true state of the upload.",
        "user_impact": "User sees an error for something that worked, retries, and may create duplicates "
                       "or give up and lose trust in the app.",
        "suggested_action": "After a timeout, ask the server for the upload's status (by upload_id) before "
                            "showing an error or retrying. Show 'checking...' instead of 'failed'.",
    },
    "UPLOAD_NOT_SAVED": {
        "title": "Upload never reached the server",
        "severity": 4,
        "rule": "upload AND client did not get a success reply AND server receipt_count == 0",
        "description": "The upload failed and the server has no copy of it.",
        "expected_behaviour": "The upload is eventually saved, or the user's file is kept on the phone "
                            "and retried later.",
        "user_impact": "The user's photo/form is lost unless the app keeps it and retries.",
        "suggested_action": "Keep the file in a local outbox and retry with back-off when the "
                            "connection returns; show the pending state to the user.",
    },
    "UPLOAD_INTEGRITY": {
        "title": "Upload reported success but the server copy is missing or different",
        "severity": 5,
        "rule": "upload AND client got success AND (receipt_count == 0 OR server sha256 != client sha256)",
        "description": "The client was told the upload worked, but the server's copy does not match.",
        "expected_behaviour": "A success reply means the exact file is stored.",
        "user_impact": "Silent data loss or corruption.",
        "suggested_action": "Compare a checksum (SHA-256) on the server and only reply success when it matches.",
    },
    "RECOVERY_FAILURE": {
        "title": "Operation did not recover after retrying",
        "severity": 4,
        "rule": "retries > 0 AND no attempt succeeded",
        "description": "The client retried, but every attempt failed, so the operation never recovered.",
        "expected_behaviour": "A short, temporary failure is hidden from the user by retrying.",
        "user_impact": "The user is stuck on an error even though the problem was temporary.",
        "suggested_action": "Use more retries with increasing waits (exponential back-off) for temporary "
                            "errors like 503, and give the user a clear 'Try again' button.",
    },
    "TIMEOUT": {
        "title": "Request timed out",
        "severity": 3,
        "rule": "final operation status is TIMEOUT (requests raised ReadTimeout)",
        "description": "No reply arrived within the client's timeout.",
        "expected_behaviour": "The request completes, or the app shows a clear message and offers a retry.",
        "user_impact": "Spinner followed by an error; screen may stay empty.",
        "suggested_action": "Choose timeouts that fit slow networks, show progress, and handle the "
                            "Timeout exception with a helpful message and a retry option.",
    },
    "HTTP_FAILURE": {
        "title": "Server returned an error status",
        "severity": 3,
        "rule": "final operation status is HTTP_ERROR (status code not 2xx)",
        "description": "The server answered with an error status code (for example 503).",
        "expected_behaviour": "The request succeeds, or the app explains the error and retries.",
        "user_impact": "Operation fails; if the app ignores the status code the user sees nothing happen.",
        "suggested_action": "Check the status code on every response, retry 5xx errors with back-off, "
                            "and show a clear message.",
    },
    "REQUEST_LOST_SIMULATED": {
        "title": "Request lost (SIMULATED packet loss)",
        "severity": 2,
        "rule": "final operation status is LOST_SIMULATED",
        "description": "SIMULATION: the packet-loss model dropped the last attempt. This is not a "
                       "measurement of a real network.",
        "expected_behaviour": "Lost requests are retried until they get through.",
        "user_impact": "Same as a timeout for the user, if loss really happens on their network.",
        "suggested_action": "Use retries with back-off; test on a real throttled network to confirm.",
    },
}

CONFIDENCE = {
    "GENUINE": "High - genuine local HTTP measurement.",
    "FAULT_INJECTION": "High for the app's behaviour (real requests, real exceptions, real server records). "
                       "The bad condition itself was injected on purpose, not measured on a real network.",
    "SIMULATED": "Low - the condition is SIMULATED. Treat as a hypothesis to confirm on a real network.",
}


def finding_id(code, operation, profile, category):
    """A short, stable ID: the same failure type in the same situation always gets the same ID."""
    text = f"{code}|{operation}|{profile}|{category}"
    return "F-" + hashlib.sha1(text.encode()).hexdigest()[:6].upper()


def detect(record):
    """Apply every detector rule to one test record. Returns a list of detections."""
    status = record["operation_status"]
    config = record["config"]
    evidence = record.get("evidence", {})
    check = evidence.get("server_check") or {}
    is_upload = record["operation"] == "upload"
    got_success = status == "SUCCESS"
    hits = []  # (code, explanation)

    if status == "TIMEOUT":
        error = record.get("error") or {}
        hits.append(("TIMEOUT", f"{error.get('type', 'Timeout')} after {config['timeout']} s "
                                f"(server delay was {config['server_delay']} s)."))
    if status == "HTTP_ERROR":
        hits.append(("HTTP_FAILURE", f"Final reply was HTTP {evidence.get('final_http_status')}."))
    if status == "LOST_SIMULATED":
        hits.append(("REQUEST_LOST_SIMULATED",
                     f"{evidence.get('simulated_drops')} of {evidence.get('attempts_made')} attempts "
                     f"dropped by the simulation (loss_rate {config['loss_rate']}, seed {config['seed']})."))
    if config["retries"] > 0 and not got_success:
        hits.append(("RECOVERY_FAILURE", f"All {evidence.get('attempts_made')} attempts failed "
                                         f"(retries = {config['retries']})."))

    if is_upload and check.get("checked"):
        receipts = check["receipt_count"]
        if not got_success and receipts >= 1:
            hits.append(("AMBIGUOUS_UPLOAD", f"Client saw {status}, but the server had saved "
                                             f"{receipts} copy(ies) of upload {evidence['upload_id']}."))
        if receipts > 1:
            hits.append(("DUPLICATE_UPLOAD", f"Server saved {receipts} copies of one upload "
                                             f"(receipts {', '.join(check['receipt_ids'])})."))
        if not got_success and receipts == 0:
            hits.append(("UPLOAD_NOT_SAVED", f"Client saw {status} and the server has no copy."))
        if got_success and (receipts == 0 or any(h != evidence["client_sha256"] for h in check["server_sha256"])):
            hits.append(("UPLOAD_INTEGRITY", "Server copy missing or its SHA-256 differs from the client's."))

    detections = []
    for code, explanation in hits:
        info = DETECTORS[code]
        detections.append({
            "code": code,
            "finding_id": finding_id(code, record["operation"], record["profile"], record["evidence_category"]),
            "title": info["title"],
            "severity": info["severity"],
            "rule": info["rule"],
            "explanation": explanation,
        })
    return detections


# ---------------------------------------------------------------- reproduction

def reproduction_command(config):
    """The exact terminal command that re-runs this test with the same settings."""
    parts = ["python tester.py",
             f"--operation {config['operation']}", f"--profile {config['profile']}",
             f"--timeout {config['timeout']}", f"--delay {config['server_delay']}",
             f"--fail-count {config['fail_count']}", f"--retries {config['retries']}",
             f"--loss-rate {config['loss_rate']}", f"--seed {config['seed']}"]
    if config["operation"] == "upload":
        parts.append(f"--upload-size-kb {config['upload_size_kb']}")
        if config["idempotent_server"]:
            parts.append("--idempotent-server")
    parts.append(f"--base-url {config['base_url']}")
    return " ".join(parts)


def curl_commands(config):
    """Commands that reproduce the failure WITHOUT PhoneReality (one attempt only).

    Returns None for simulated packet loss: that condition only exists inside PhoneReality.
    """
    if config["loss_rate"] > 0:
        return None
    base = config["base_url"].rstrip("/")
    query = f"delay={config['server_delay']}&fail_count={config['fail_count']}&fault_key=repro-1"
    if config["operation"] == "load_data":
        return [f'curl.exe -i -m {config["timeout"]} "{base}/api/data?{query}"']
    idem = 1 if config["idempotent_server"] else 0
    return [
        f'curl.exe -i -m {config["timeout"]} -F "file=@README.md" -F "upload_id=repro-1" '
        f'"{base}/api/upload?{query}&idempotent={idem}"',
        f'curl.exe "{base}/api/uploads/repro-1"',
    ]


def reproduction_steps(record):
    config = record["config"]
    steps = [
        "Start the demo app in its own terminal:  python demo_app.py",
        f"In PhoneReality open 'Run Tests' and choose operation '{record['operation_label']}' "
        f"and profile '{record['profile_label']}'.",
        f"Set: client timeout {config['timeout']} s, server delay {config['server_delay']} s, "
        f"injected 503 failures {config['fail_count']}, retries {config['retries']}, "
        f"simulated loss rate {config['loss_rate']}, seed {config['seed']}"
        + (f", upload size {config['upload_size_kb']} KB, server de-duplication "
           f"{'ON' if config['idempotent_server'] else 'OFF'}" if config["operation"] == "upload" else "")
        + ".",
        "Click 'Run test' (or press 'Re-run original configuration' on the Reproduce page).",
        f"Expected operation status: {record['expected_status']}. "
        f"The finding is reproduced if the same detector fires again.",
    ]
    return steps


# ---------------------------------------------------------------- findings

def build_findings(records, assumptions):
    """Group all detections into findings, newest evidence first, sorted by priority."""
    groups = {}
    for record in records:
        for detection in record.get("detections", []):
            groups.setdefault(detection["finding_id"], []).append((record, detection))

    findings = []
    for fid, items in groups.items():
        first_record, first_detection = items[0]
        latest_record, latest_detection = items[-1]
        info = DETECTORS[first_detection["code"]]
        profile, operation = first_record["profile"], first_record["operation"]
        category = first_record["evidence_category"]
        same_situation = [r for r in records if r["profile"] == profile and r["operation"] == operation
                          and r["evidence_category"] == category]
        score = scoring.score_finding(info["severity"], profile, operation, assumptions)
        latest_config = latest_record["config"]
        findings.append({
            "finding_id": fid,
            "code": first_detection["code"],
            "title": info["title"],
            "description": info["description"],
            "severity": info["severity"],
            "profile": profile,
            "profile_label": first_record["profile_label"],
            "operation": operation,
            "operation_label": first_record["operation_label"],
            "evidence_category": category,
            "detector_rule": info["rule"],
            "observed_behaviour": latest_detection["explanation"],
            "expected_behaviour": info["expected_behaviour"],
            "actual_behaviour": latest_record["actual_outcome"],
            "evidence": {
                "latest_test_id": latest_record["test_id"],
                "operation_status": latest_record["operation_status"],
                "final_http_status": latest_record["evidence"].get("final_http_status"),
                "error": latest_record.get("error"),
                "duration_s": latest_record["duration_s"],
                "client_timeout_s": latest_config["timeout"],
                "server_delay_s": latest_config["server_delay"],
                "attempts_made": latest_record["evidence"].get("attempts_made"),
                "upload_id": latest_record["evidence"].get("upload_id"),
                "client_sha256": latest_record["evidence"].get("client_sha256"),
                "server_check": latest_record["evidence"].get("server_check"),
            },
            "confidence": CONFIDENCE[category],
            "measured_frequency": f"Detected in {len(items)} of {len(same_situation)} test run(s) of this "
                                  f"operation + profile. (Local runs only - not a population rate.)",
            "occurrences": len(items),
            "runs_in_same_situation": len(same_situation),
            "user_impact": info["user_impact"],
            "suggested_action": info["suggested_action"],
            "priority": score,
            "original_test_id": first_record["test_id"],
            "original_config": first_record.get("config"),
            "original_expected_status": first_record["expected_status"],
            "original_observed_status": first_record["operation_status"],
            "reproduction_steps": reproduction_steps(first_record),
            "reproduction_command": reproduction_command(first_record["config"]),
            "curl_commands": curl_commands(first_record["config"]),
            "test_ids": [r["test_id"] for r, _ in items],
        })
    findings.sort(key=lambda f: (f["priority"]["percent"], f["severity"]), reverse=True)
    return findings

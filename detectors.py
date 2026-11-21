"""Turn each saved run into a small set of explainable findings.

The checks are ordinary Python conditions in ``detect``. Given the same run,
they always return the same detections. ``build_findings`` groups repeat
detections from the same operation and profile, then adds the evidence, replay
steps and priority score a developer needs to investigate them.
"""

import hashlib

import scoring

DETECTORS = {
    "DUPLICATE_UPLOAD": {
        "title": "One upload ended up with multiple server copies",
        "severity": 5,
        "rule": "upload AND server receipt_count > 1 for a single upload_id",
        "description": "A retry reached the server after the first upload had already been saved. The server kept both copies.",
        "expected_behaviour": "One upload should create one saved copy, even if the client retries.",
        "user_impact": "In a real app, a repeated action could create duplicate posts, orders or payments.",
        "suggested_action": "Give each upload an idempotency key and have the server accept that key only once. Turn on server de-duplication in Run Tests to try the fix.",
    },
    "AMBIGUOUS_UPLOAD": {
        "title": "The upload succeeded, but the client showed an error",
        "severity": 4,
        "rule": "upload AND client did not get a success reply AND server receipt_count >= 1",
        "description": "The server saved the file, but its reply arrived after the client timed out. The client reported failure even though the upload is there.",
        "expected_behaviour": "After a timeout, the app should check whether the upload was saved before telling the user it failed.",
        "user_impact": "The user may try again and create a duplicate, or abandon something that actually worked.",
        "suggested_action": "Use the upload ID to check the server's receipt before showing an error or retrying. Let the user know the app is checking.",
    },
    "UPLOAD_NOT_SAVED": {
        "title": "The upload did not reach the server",
        "severity": 4,
        "rule": "upload AND client did not get a success reply AND server receipt_count == 0",
        "description": "The request failed, and the server has no receipt for the upload.",
        "expected_behaviour": "Keep the file safe and retry later, or tell the user clearly that it was not saved.",
        "user_impact": "Without a saved copy or a retry, the user's photo or form could be lost.",
        "suggested_action": "Keep the file in a local pending queue and retry with a short wait between attempts when the connection returns.",
    },
    "UPLOAD_INTEGRITY": {
        "title": "The saved file does not match the successful upload",
        "severity": 5,
        "rule": "upload AND client got success AND (receipt_count == 0 OR server sha256 != client sha256)",
        "description": "The client received a success response, but the server has no matching receipt or file hash.",
        "expected_behaviour": "A success response should mean the server has the exact file the client sent.",
        "user_impact": "The app may appear to work while the user's data is missing or damaged.",
        "suggested_action": "Compare the file's SHA-256 hash on both sides, and only report success when they match.",
    },
    "RECOVERY_FAILURE": {
        "title": "The retries did not get the request through",
        "severity": 4,
        "rule": "retries > 0 AND no attempt succeeded",
        "description": "The client tried again, but none of the attempts succeeded.",
        "expected_behaviour": "A short outage should recover when another attempt gets through, or the app should explain what the user can do next.",
        "user_impact": "The user is left at an error even if the service comes back a moment later.",
        "suggested_action": "For temporary errors such as HTTP 503, retry with increasing waits and give the user a clear way to try again.",
    },
    "TIMEOUT": {
        "title": "The server replied too late",
        "severity": 3,
        "rule": "final operation status is TIMEOUT (requests raised ReadTimeout)",
        "description": "The client stopped waiting before the server sent its reply.",
        "expected_behaviour": "Finish the request, or explain that it is taking longer and offer a safe retry.",
        "user_impact": "The screen may stop loading without telling the user whether anything happened.",
        "suggested_action": "Choose a timeout that fits the operation, show progress, and handle timeouts with a useful message and retry option.",
    },
    "HTTP_FAILURE": {
        "title": "The server returned an error",
        "severity": 3,
        "rule": "final operation status is HTTP_ERROR (status code not 2xx)",
        "description": "The server answered, but its status code shows that the request failed (for example, HTTP 503).",
        "expected_behaviour": "The request succeeds, or the app explains the problem and retries when it is safe to do so.",
        "user_impact": "If the app ignores the response code, the action can fail without any useful message.",
        "suggested_action": "Check every response code. Retry temporary 5xx errors with a wait between attempts, and show the user what happened.",
    },
    "REQUEST_LOST_SIMULATED": {
        "title": "A request was dropped in the simulation",
        "severity": 2,
        "rule": "final operation status is LOST_SIMULATED",
        "description": "The packet-loss model dropped the last attempt before it was sent. This result comes from a simulation, not a real network.",
        "expected_behaviour": "Retry a dropped request when it is safe, and tell the user if it still does not get through.",
        "user_impact": "A real dropped request can feel like a timeout to the user.",
        "suggested_action": "Use retries with a short wait between them, then confirm the behavior on a real network.",
    },
}

CONFIDENCE = {
    "GENUINE": "High - genuine local HTTP measurement.",
    "FAULT_INJECTION": "High for the app's behaviour (real requests, real exceptions, real server records). "
                       "The bad condition itself was injected on purpose, not measured on a real network.",
    "SIMULATED": "Low - the condition is SIMULATED. Treat as a hypothesis to confirm on a real network.",
}


def finding_id(code, operation, profile, category):
    """Give the same kind of failure the same short ID each time."""
    text = f"{code}|{operation}|{profile}|{category}"
    return "F-" + hashlib.sha1(text.encode()).hexdigest()[:6].upper()


def detect(record):
    """Check one run against each rule and return the ones that match."""
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
            copies = "copy" if receipts == 1 else "copies"
            hits.append(("AMBIGUOUS_UPLOAD", f"The client reported {status}, but the server saved "
                                             f"{receipts} {copies} of upload {evidence['upload_id']}."))
        if receipts > 1:
            hits.append(("DUPLICATE_UPLOAD", f"The server saved {receipts} copies of one upload "
                                             f"(receipts: {', '.join(check['receipt_ids'])})."))
        if not got_success and receipts == 0:
            hits.append(("UPLOAD_NOT_SAVED", f"The client reported {status}, and the server has no saved copy."))
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


# Reproducing a finding

def reproduction_command(config):
    """Build a terminal command that uses the run's saved settings."""
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
    """Build one-attempt curl commands when the condition can be reproduced outside BudgetBench.

    Simulated packet loss has no curl equivalent, so return ``None`` for that case.
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
        f"In BudgetBench, open 'Run Tests' and choose '{record['operation_label']}' "
        f"and profile '{record['profile_label']}'.",
        f"Set: client timeout {config['timeout']} s, server delay {config['server_delay']} s, "
        f"injected 503 failures {config['fail_count']}, retries {config['retries']}, "
        f"simulated loss rate {config['loss_rate']}, seed {config['seed']}"
        + (f", upload size {config['upload_size_kb']} KB, server de-duplication "
           f"{'ON' if config['idempotent_server'] else 'OFF'}" if config["operation"] == "upload" else "")
        + ".",
        "Choose 'Run test', or select 'Re-run original configuration' on the Reproduce page.",
        f"We expect the operation to end with {record['expected_status']}. "
        "The issue is reproduced if the same detector fires again.",
    ]
    return steps


# Building findings from saved runs

def build_findings(records, assumptions):
    """Group repeat detections, keep the latest evidence and sort by priority."""
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

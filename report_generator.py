"""
report_generator.py - turns the saved test records and findings into downloadable files.

  JSON     - everything (records, findings, assumptions), for tools and for re-checking.
  CSV      - one row per test, opens in Excel / Google Sheets.
  Findings - one row per finding (CSV) and a readable Markdown report.

All functions return text. The dashboard hands that text to a download button.
"""

import csv
import io
import json
from datetime import datetime

DISCLAIMER = ("Results come from a local demo app on a laptop. FAULT_INJECTION results are genuine "
              "HTTP outcomes caused by deliberate faults; SIMULATED results depend on a model. "
              "None of these are measurements of real Indian mobile networks or Android devices.")

RESULT_COLUMNS = [
    "test_id", "timestamp", "profile", "operation", "evidence_category",
    "timeout", "server_delay", "fail_count", "retries", "loss_rate", "seed",
    "upload_size_kb", "idempotent_server",
    "expected_status", "operation_status", "verdict", "duration_s",
    "final_http_status", "error_type", "error_message", "attempts_made",
    "server_receipt_count", "finding_ids", "actual_outcome",
]

FINDING_COLUMNS = [
    "finding_id", "title", "severity", "likelihood", "impact_weight", "priority_raw",
    "priority_percent", "priority_band", "profile", "operation", "evidence_category",
    "occurrences", "runs_in_same_situation", "observed_behaviour", "expected_behaviour",
    "suggested_action", "original_test_id", "reproduction_command",
]


def result_row(record):
    """Flatten one nested test record into a single CSV row."""
    config = record["config"]
    error = record.get("error") or {}
    check = record["evidence"].get("server_check") or {}
    return {
        "test_id": record["test_id"],
        "timestamp": record["timestamp"],
        "profile": record["profile"],
        "operation": record["operation"],
        "evidence_category": record["evidence_category"],
        "timeout": config["timeout"],
        "server_delay": config["server_delay"],
        "fail_count": config["fail_count"],
        "retries": config["retries"],
        "loss_rate": config["loss_rate"],
        "seed": config["seed"],
        "upload_size_kb": config["upload_size_kb"] if record["operation"] == "upload" else "",
        "idempotent_server": config["idempotent_server"] if record["operation"] == "upload" else "",
        "expected_status": record["expected_status"],
        "operation_status": record["operation_status"],
        "verdict": record["verdict"],
        "duration_s": record["duration_s"],
        "final_http_status": record["evidence"].get("final_http_status"),
        "error_type": error.get("type"),
        "error_message": error.get("message"),
        "attempts_made": record["evidence"].get("attempts_made"),
        "server_receipt_count": check.get("receipt_count", ""),
        "finding_ids": ";".join(record.get("finding_ids", [])),
        "actual_outcome": record["actual_outcome"],
    }


def finding_row(finding):
    priority = finding["priority"]
    return {
        "finding_id": finding["finding_id"],
        "title": finding["title"],
        "severity": priority["severity"],
        "likelihood": priority["likelihood"],
        "impact_weight": priority["impact_weight"],
        "priority_raw": priority["raw"],
        "priority_percent": priority["percent"],
        "priority_band": priority["band"],
        "profile": finding["profile"],
        "operation": finding["operation"],
        "evidence_category": finding["evidence_category"],
        "occurrences": finding["occurrences"],
        "runs_in_same_situation": finding["runs_in_same_situation"],
        "observed_behaviour": finding["observed_behaviour"],
        "expected_behaviour": finding["expected_behaviour"],
        "suggested_action": finding["suggested_action"],
        "original_test_id": finding["original_test_id"],
        "reproduction_command": finding["reproduction_command"],
    }


def _to_csv(rows, columns):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def results_csv(records):
    return _to_csv([result_row(r) for r in records], RESULT_COLUMNS)


def findings_csv(findings):
    return _to_csv([finding_row(f) for f in findings], FINDING_COLUMNS)


def full_json(records, findings, assumptions):
    report = {
        "tool": "PhoneReality",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": DISCLAIMER,
        "test_count": len(records),
        "finding_count": len(findings),
        "assumptions": assumptions,
        "findings": findings,
        "results": records,
    }
    return json.dumps(report, indent=2, ensure_ascii=False)


def findings_markdown(findings, assumptions):
    lines = [
        "# PhoneReality findings report",
        "",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"> {DISCLAIMER}",
        "",
        "Priority = Severity x Likelihood x Impact weight (each 1-5, max 125).",
        f"Likelihood and impact weight are assumptions: {assumptions['source']}",
        "",
    ]
    if not findings:
        lines.append("No findings yet. Run some tests first.")
    for f in findings:
        p = f["priority"]
        lines += [
            f"## {f['finding_id']} - {f['title']}",
            "",
            f"- **Priority:** {p['percent']}% ({p['band']}) - {p['formula']}",
            f"- **Profile / operation:** {f['profile_label']} / {f['operation_label']}",
            f"- **Evidence category:** {f['evidence_category']}",
            f"- **Detector rule:** `{f['detector_rule']}`",
            f"- **Observed:** {f['observed_behaviour']}",
            f"- **Expected:** {f['expected_behaviour']}",
            f"- **Actual:** {f['actual_behaviour']}",
            f"- **Measured frequency:** {f['measured_frequency']}",
            f"- **Confidence:** {f['confidence']}",
            f"- **User impact:** {f['user_impact']}",
            f"- **Suggested action:** {f['suggested_action']}",
            "",
            "**Reproduce:**",
            "",
        ]
        lines += [f"{i}. {step}" for i, step in enumerate(f["reproduction_steps"], start=1)]
        lines += ["", "```powershell", f["reproduction_command"], "```", ""]
    return "\n".join(lines)

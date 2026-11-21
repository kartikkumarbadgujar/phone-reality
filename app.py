"""
app.py - the BudgetBench dashboard (Streamlit).

Start it with:      streamlit run app.py
The demo app must be running in ANOTHER terminal for tests to run:   python demo_app.py

This file only draws the screens. The real work happens in:
  tester.py            - sends the real HTTP requests
  detectors.py         - decides what counts as a failure
  scoring.py           - priority score
  report_generator.py  - downloads
  storage.py           - reading/writing data/results.json
"""

import os
import ipaddress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import streamlit as st

import demo_app
import detectors
import report_generator
import scoring
import storage
import tester
from core import adb_controller
from database import db_manager

st.set_page_config(page_title="BudgetBench", page_icon="📱", layout="wide", initial_sidebar_state="expanded")

PAGES = ["Dashboard", "Projects", "Device Lab", "Test Environment", "Run Tests",
         "Live Monitoring", "Condition Matrix", "Failure Analysis", "Findings", "Reproduce",
         "Test History", "Reports", "Neo4j Graph Explorer", "Challenge Readiness Centre", "Settings"]

# Streamlit "badge" markdown, e.g. :green-badge[PASS]. Every badge has a text label,
# so meaning never depends on colour alone.
CATEGORY_BADGES = {
    "GENUINE": ":green-badge[GENUINE MEASUREMENT]",
    "FAULT_INJECTION": ":blue-badge[CONTROLLED FAULT INJECTION]",
    "SIMULATED": ":orange-badge[SIMULATED]",
}
STATUS_BADGES = {
    "SUCCESS": ":green-badge[SUCCESS]",
    "TIMEOUT": ":orange-badge[TIMEOUT]",
    "HTTP_ERROR": ":red-badge[HTTP_ERROR]",
    "LOST_SIMULATED": ":gray-badge[LOST (SIMULATED)]",
    "CONNECTION_ERROR": ":red-badge[CONNECTION_ERROR]",
    "UNPREDICTABLE": ":gray-badge[UNPREDICTABLE]",
}
VERDICT_BADGES = {
    "PASS": ":green-badge[PASS]",
    "FAIL": ":red-badge[FAIL]",
    "INCONCLUSIVE": ":gray-badge[INCONCLUSIVE]",
    "ERROR": ":red-badge[ERROR]",
}
BAND_BADGES = {"High": ":red-badge[High priority]", "Medium": ":orange-badge[Medium priority]",
               "Low": ":gray-badge[Low priority]"}
# Chart colours for operation statuses (status palette: good / warning / critical / neutral)
STATUS_COLORS = {"SUCCESS": "#0ca30c", "TIMEOUT": "#fab219", "HTTP_ERROR": "#d03b3b",
                 "LOST_SIMULATED": "#8a8a8a"}

NAV_LABELS = {
    "Dashboard": "◈   Overview",
    "Projects": "▦   Projects",
    "Device Lab": "◉   Device lab",
    "Test Environment": "⚙   Test setup",
    "Run Tests": "▶   Run tests",
    "Live Monitoring": "⌁   Live monitor",
    "Condition Matrix": "⊞   Condition matrix",
    "Failure Analysis": "⌕   Failure analysis",
    "Findings": "⚑   Findings",
    "Reproduce": "↻   Reproduce",
    "Test History": "◷   Test history",
    "Reports": "▤   Reports",
    "Neo4j Graph Explorer": "⋈   Graph explorer",
    "Challenge Readiness Centre": "◎   Challenge centre",
    "Settings": "⚙   Settings",
}


# ONLINE MODE. Streamlit Community Cloud runs only this one program, so there is no second terminal
# for demo_app.py. Online, we start the demo app inside this program instead (see
# demo_app.start_in_background). Streamlit Cloud keeps apps under /mount/src/; PHONEREALITY_CLOUD=1
# switches the same mode on anywhere else.
ONLINE_MODE = (Path(__file__).resolve().as_posix().startswith("/mount/src/")
               or os.environ.get("PHONEREALITY_CLOUD") == "1")


@st.cache_resource
def start_builtin_demo_app():
    """Start the demo app once per server (not once per visitor) and return its address."""
    port = int(os.environ.get("PHONEREALITY_BUILTIN_PORT", demo_app.DEFAULT_PORT))
    _, base_url = demo_app.start_in_background(storage.data_dir(), port=port)
    return base_url


def badge(table, key):
    return table.get(key, f"`{key}`")


def load_everything():
    records, warning = storage.load_results()
    # Index legacy JSON sessions at startup so old PhoneReality history appears in BudgetBench analytics.
    for record in records:
        db_manager.index_run(record)
    assumptions = storage.load_assumptions(scoring.DEFAULT_ASSUMPTIONS)
    findings = detectors.build_findings(records, assumptions)
    return records, warning, assumptions, findings


def go_to_reproduce(finding_id):
    """Button callback: jump to the Reproduce page with this finding selected."""
    st.session_state["page"] = "Reproduce"
    st.session_state["repro_finding"] = finding_id


def navigate_to(page):
    """Radio navigation changes must run as callbacks before the radio is rendered."""
    st.session_state["page"] = page


def navigate_to_profile(profile):
    """Remember the chosen profile, then switch pages in the pre-render callback."""
    st.session_state["requested_profile"] = profile
    st.session_state["page"] = "Run Tests"


def page_header(title, description, eyebrow="BUDGETBENCH"):
    """Give each workspace page the same clear title and short orientation."""
    st.markdown(
        f'<div class="bb-page-head"><div class="bb-kicker">{eyebrow}</div>'
        f'<h1>{title}</h1><p class="bb-subtitle">{description}</p></div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- shared pieces

def show_record(record):
    """Show one test result: expected vs actual, verdict, detections and raw evidence."""
    with st.container(border=True):
        st.markdown(f"**{record['test_id']}** · {record['profile_label']} · {record['operation_label']} "
                    f"· {badge(CATEGORY_BADGES, record['evidence_category'])}")
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"Expected status  \n{badge(STATUS_BADGES, record['expected_status'])}")
        c2.markdown(f"Actual status  \n{badge(STATUS_BADGES, record['operation_status'])}")
        c3.markdown(f"Harness verdict  \n{badge(VERDICT_BADGES, record['verdict'])}")
        c4.markdown(f"Duration  \n**{record['duration_s']:.2f} s**")
        st.markdown(f"**What happened:** {record['actual_outcome']}")
        st.caption(f"Why we expected {record['expected_status']}: {record['expected_outcome']}")

        if record["verdict"] == "ERROR":
            st.error("The demo app could not be reached, so this run was NOT saved. "
                     "This is a setup problem, not a finding about the app.")
        elif record["detections"]:
            for d in record["detections"]:
                st.warning(f"**Finding {d['finding_id']} — {d['title']}** (severity {d['severity']}/5)  \n"
                           f"{d['explanation']}  \nDetector rule: `{d['rule']}`")
        else:
            st.success("No failure detected for this run.")

        with st.expander("Attempts, evidence and exact configuration"):
            attempts = pd.DataFrame(record["attempts"])[
                ["attempt", "status", "http_status", "elapsed_s", "error_type", "error_message"]]
            st.dataframe(attempts, hide_index=True, width="stretch")
            left, right = st.columns(2)
            left.markdown("**Evidence**")
            left.json(record["evidence"])
            right.markdown("**Configuration used**")
            right.json(record["config"])


def show_finding(finding):
    p = finding["priority"]
    st.subheader(f"{finding['finding_id']} — {finding['title']}")
    st.markdown(f"{badge(BAND_BADGES, p['band'])} {badge(CATEGORY_BADGES, finding['evidence_category'])} "
                f"Severity **{finding['severity']}/5** · {finding['profile_label']} · "
                f"{finding['operation_label']}")
    st.write(finding["description"])

    left, right = st.columns(2)
    with left:
        st.markdown(f"**Observed:** {finding['observed_behaviour']}")
        st.markdown(f"**Expected:** {finding['expected_behaviour']}")
        st.markdown(f"**Actual (latest run):** {finding['actual_behaviour']}")
        st.markdown(f"**Detector rule:** `{finding['detector_rule']}`")
    with right:
        st.markdown(f"**Estimated user impact:** {finding['user_impact']}")
        st.markdown(f"**Suggested developer action:** {finding['suggested_action']}")
        st.markdown(f"**Confidence:** {finding['confidence']}")
        st.markdown(f"**Measured frequency:** {finding['measured_frequency']}")

    st.markdown("**How the priority score was calculated**")
    factors = pd.DataFrame([
        {"Factor": "Severity", "Value": p["severity"], "Source": "Fixed per detector type (detectors.py)"},
        {"Factor": "Likelihood", "Value": p["likelihood"],
         "Source": f"ASSUMPTION for profile '{finding['profile']}' (editable above)"},
        {"Factor": "Impact weight", "Value": p["impact_weight"],
         "Source": f"ASSUMPTION for operation '{finding['operation']}' (editable above)"},
    ])
    st.dataframe(factors, hide_index=True, width="stretch")
    st.code(f"Priority = Severity x Likelihood x Impact = {p['formula']}", language=None)

    with st.expander("Evidence from the latest run"):
        st.json(finding["evidence"])
        st.caption("Seen in tests: " + ", ".join(finding["test_ids"]))


# ---------------------------------------------------------------- pages

def page_dashboard(records, findings):
    st.markdown(
        '<section class="bb-hero"><div class="bb-hero-content">'
        '<div class="bb-hero-label">HackFront India · PS T1.2</div>'
        '<div class="bb-hero-title">Test beyond the flagship.</div>'
        '<p class="bb-hero-copy">Find reliability issues under slower responses and intermittent service. '
        'Capture evidence, understand impact, and reproduce the behavior.</p>'
        '</div></section>', unsafe_allow_html=True)
    action_left, action_right = st.columns([1, 1])
    action_left.button("▶   Start a test", type="primary", use_container_width=True,
                       key="dashboard_start_test", on_click=navigate_to, args=("Run Tests",))
    action_right.button("＋   Create a project", use_container_width=True,
                        key="dashboard_create_project", on_click=navigate_to, args=("Projects",))
    st.markdown('<div class="bb-section-heading"><h3>Workspace at a glance</h3></div>', unsafe_allow_html=True)
    db_counts = db_manager.counts()
    request_attempts = sum(len(record.get("attempts", [])) for record in records)
    open_findings = sum(f.get("status", "Open") != "Resolved" for f in findings)
    summary = st.columns(5)
    summary[0].metric("Projects", db_counts["projects"])
    summary[1].metric("Test sessions", db_counts["test_sessions"])
    summary[2].metric("HTTP attempts", request_attempts)
    summary[3].metric("Device snapshots", db_counts["device_snapshots"])
    summary[4].metric("Open findings", open_findings)
    if not records:
        st.markdown(
            '<div class="bb-empty"><div class="bb-empty-title">Your reliability workspace is ready</div>'
            '<div class="bb-empty-copy">There are no test runs yet. Start with the built-in upload scenario '
            'to see how delayed responses can create ambiguous uploads and how idempotency prevents duplicates.</div></div>',
            unsafe_allow_html=True)
        st.caption("Metrics fill in from saved projects, test runs, findings, and device snapshots. No sample results are shown.")
        return

    total = len(records)
    succeeded = sum(r["operation_status"] == "SUCCESS" for r in records)
    passed = sum(r["verdict"] == "PASS" for r in records)
    high = sum(f["priority"]["band"] == "High" for f in findings)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tests executed", total)
    c2.metric("Operations succeeded", succeeded)
    c3.metric("Operations failed (app)", total - succeeded)
    c4.metric("Harness verdict not PASS", total - passed)
    c5.metric("Findings (high priority)", f"{len(findings)} ({high})")
    st.caption("**Operation failed** = the app's request failed (often on purpose, because we injected a fault). "
               "**Harness verdict** = whether BudgetBench reproduced the outcome it predicted from the settings. "
               "A deliberately caused timeout that is observed as predicted counts as a verdict PASS.")

    by_category = {c: sum(r["evidence_category"] == c for r in records) for c in CATEGORY_BADGES}
    c1, c2, c3 = st.columns(3)
    c1.metric("Genuine measurements", by_category["GENUINE"])
    c2.metric("Controlled fault injections", by_category["FAULT_INJECTION"])
    c3.metric("SIMULATED", by_category["SIMULATED"])

    st.markdown('<div class="bb-section-heading"><h3>Outcomes by condition</h3></div>', unsafe_allow_html=True)
    table = pd.DataFrame([{"Profile": tester.PROFILES[r["profile"]]["short"], "Status": r["operation_status"]}
                          for r in records])
    counts = table.groupby(["Profile", "Status"]).size().unstack(fill_value=0)
    statuses = [s for s in STATUS_COLORS if s in counts.columns]
    # In a horizontal bar chart Streamlit puts x_label on the category axis and y_label on the value axis.
    st.bar_chart(counts[statuses], color=[STATUS_COLORS[s] for s in statuses], horizontal=True,
                 x_label="", y_label="Number of tests", height=90 + 45 * len(counts))

    st.markdown('<div class="bb-section-heading"><h3>Recent activity</h3></div>', unsafe_allow_html=True)
    recent = pd.DataFrame([{
        "Time": r["timestamp"][11:], "Test ID": r["test_id"], "Profile": r["profile_label"],
        "Operation": r["operation"], "Evidence": r["evidence_category"], "Expected": r["expected_status"],
        "Actual": r["operation_status"], "Verdict": r["verdict"], "Duration (s)": r["duration_s"],
        "Findings": ", ".join(r["finding_ids"]) or "—",
    } for r in reversed(records[-15:])])
    st.dataframe(recent, hide_index=True, width="stretch")


def page_condition_matrix(records):
    page_header("Condition matrix", "Compare each runnable profile, the evidence it produces, and what remains outside this build.", "TEST DESIGN")
    st.caption("What each condition profile does, how trustworthy its evidence is, and the latest real result. "
               "The evidence category of a run is decided from the settings actually used, not the profile name.")

    def latest(profile, operation):
        runs = [r for r in records if r["profile"] == profile and r["operation"] == operation]
        if not runs:
            return "not run yet"
        last = runs[-1]
        return f"{last['operation_status']} (verdict {last['verdict']})"

    rows = []
    for key, p in tester.PROFILES.items():
        d = p["defaults"]
        rows.append({
            "Profile": p["label"],
            "Evidence category": tester.CATEGORY_LABELS[p["evidence_category"]],
            "Latest: load data": latest(key, "load_data"), "Latest: upload": latest(key, "upload"),
            "Expected behaviour": p["expected_behaviour"],
            "What happens": p["what_happens"],
            "Default parameters": (f"timeout {d['timeout']} s, delay {d['server_delay']} s, "
                                   f"503s {d['fail_count']}, retries {d['retries']}, loss {d['loss_rate']}"),
        })
    st.subheader("Implemented profiles")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Not implemented — future work")
    st.caption("There are deliberately no buttons for these: we will not fake device-level measurements.")
    future = pd.DataFrame([{"Profile": name, "Why it is not in this prototype": reason}
                           for name, reason in tester.FUTURE_PROFILES.items()])
    st.dataframe(future, hide_index=True, width="stretch")

    st.markdown("**Evidence categories**")
    st.markdown(f"- {CATEGORY_BADGES['GENUINE']} a real request to the local demo app, with no fault.\n"
                f"- {CATEGORY_BADGES['FAULT_INJECTION']} the demo app misbehaves on purpose (delay, HTTP 503). "
                "The client's reaction (real timeout, real status code, real saved file) is genuinely measured. "
                "It is **not** a measurement of a real mobile network.\n"
                f"- {CATEGORY_BADGES['SIMULATED']} a model decides what happens (seeded packet loss). "
                "Treat results as hypotheses.")


def page_run_tests(base_url):
    page_header("Run a test", "Choose an operation and condition, review the expected outcome, then run the real HTTP scenario.", "TEST LAB")
    st.markdown('<div class="bb-section-heading"><h3>1. Choose the scenario</h3></div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    operation = c1.selectbox("Operation", list(tester.OPERATIONS),
                             format_func=lambda k: tester.OPERATIONS[k]["label"])
    requested_profile = st.session_state.pop("requested_profile", None)
    profile = c2.selectbox("Condition profile", list(tester.PROFILES),
                           index=list(tester.PROFILES).index(requested_profile) if requested_profile in tester.PROFILES else 0,
                           format_func=lambda k: tester.PROFILES[k]["label"])
    info = tester.PROFILES[profile]
    with st.container(border=True):
        st.markdown(f"{badge(CATEGORY_BADGES, info['evidence_category'])} {info['description']}")

    defaults = tester.build_config(operation, profile, base_url)
    key = f"{operation}_{profile}"  # new widget keys per combination, so defaults reload when you switch
    is_upload = operation == "upload"

    st.markdown('<div class="bb-section-heading"><h3>2. Tune the conditions</h3></div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    timeout = c1.number_input("Client timeout (s)", 0.2, 60.0, float(defaults["timeout"]), 0.1,
                              key=f"timeout_{key}", help="How long the client waits for a reply before giving up.")
    server_delay = c2.number_input(
        "Server reply delay after saving (s)" if is_upload else "Server response delay (s)",
        0.0, 30.0, float(defaults["server_delay"]), 0.5, key=f"delay_{key}",
        help="Controlled fault injection: the demo app waits this long before answering.")
    fail_count = c3.number_input("Injected HTTP 503 failures", 0, 10, int(defaults["fail_count"]),
                                 key=f"fail_{key}", help="The first N requests of this test get HTTP 503.")
    c1, c2, c3 = st.columns(3)
    retries = c1.number_input("Client retries", 0, 5, int(defaults["retries"]), key=f"retries_{key}")
    loss_rate = c2.slider("SIMULATED packet loss rate", 0.0, 1.0, float(defaults["loss_rate"]), 0.05,
                          key=f"loss_{key}", help="Above 0 the whole run is labelled SIMULATED.")
    seed = c3.number_input("Seed", 0, 1_000_000, int(defaults["seed"]), key=f"seed_{key}",
                           help="Makes the simulated loss and the upload file content repeatable.")
    upload_size_kb, idempotent = defaults["upload_size_kb"], defaults["idempotent_server"]
    if is_upload:
        c1, c2 = st.columns(2)
        upload_size_kb = c1.number_input("Upload size (KB)", 1, 5120, int(upload_size_kb), key=f"size_{key}")
        idempotent = c2.checkbox("Server de-duplicates by upload_id (the fix)", value=False, key=f"idem_{key}",
                                 help="Turn on to see how an idempotency key prevents duplicate uploads.")
    repeat = st.number_input("Run this test N times in a row", 1, 10, 1, key="repeat")

    try:
        config = tester.build_config(
            operation, profile, base_url, timeout=timeout, server_delay=server_delay,
            fail_count=int(fail_count), retries=int(retries), loss_rate=loss_rate, seed=int(seed),
            upload_size_kb=int(upload_size_kb), idempotent_server=bool(idempotent))
    except ValueError as error:
        st.error(str(error))
        return

    predicted, reason = tester.predict_status(config)
    st.markdown('<div class="bb-section-heading"><h3>3. Review before running</h3></div>', unsafe_allow_html=True)
    with st.container(border=True):
        prediction_left, prediction_right = st.columns([1, 2])
        prediction_left.markdown(f"**Expected outcome**  \n{badge(STATUS_BADGES, predicted)}")
        prediction_right.markdown(f"**Evidence type**  \n{badge(CATEGORY_BADGES, tester.evidence_category(config))}")
        st.caption(reason)
    if predicted == "UNPREDICTABLE":
        st.warning(reason)

    if st.button("Run test", type="primary", use_container_width=True):
        ok, message = tester.check_server(base_url)
        if not ok:
            st.error(message)
        else:
            results = []
            progress = st.progress(0.0, text="Starting...")
            for i in range(int(repeat)):
                progress.progress(i / repeat, text=f"Running test {i + 1} of {repeat} — waiting for real replies...")
                record = tester.run_test(config)
                db_manager.index_run(record, st.session_state.get("current_project"))
                results.append(record)
            progress.progress(1.0, text=f"Finished {repeat} test(s).")
            st.session_state["last_run"] = results

    for record in st.session_state.get("last_run", []):
        show_record(record)


def page_findings(findings, assumptions):
    page_header("Findings", "Review detector-backed issues, evidence, and transparent priority scores.", "INVESTIGATE")
    with st.expander("Scoring assumptions (editable) — these are NOT measurements"):
        st.caption(scoring.ASSUMPTION_SOURCE)
        with st.form("assumptions"):
            st.markdown("**Likelihood** — how often we assume users are in this condition (1 = rare, 5 = very common)")
            cols = st.columns(len(tester.PROFILES))
            likelihood = {k: col.number_input(p["label"], 1, 5, int(assumptions["likelihood"][k]), key=f"lk_{k}")
                          for col, (k, p) in zip(cols, tester.PROFILES.items())}
            st.markdown("**Impact weight** — how much this operation matters to the user (1 = minor, 5 = critical)")
            cols = st.columns(len(tester.OPERATIONS))
            impact = {k: col.number_input(o["label"], 1, 5, int(assumptions["impact_weight"][k]), key=f"iw_{k}")
                      for col, (k, o) in zip(cols, tester.OPERATIONS.items())}
            save = st.form_submit_button("Save assumptions")
        if save:
            storage.save_assumptions({"likelihood": likelihood, "impact_weight": impact,
                                      "source": scoring.ASSUMPTION_SOURCE})
            st.rerun()
        if st.button("Reset assumptions to defaults"):
            storage.save_assumptions(scoring.DEFAULT_ASSUMPTIONS)
            for k in list(st.session_state):
                if k.startswith(("lk_", "iw_")):
                    del st.session_state[k]
            st.rerun()

    if not findings:
        st.info("No findings yet. Findings appear automatically when a detector fires during a real test run.")
        return

    summary = pd.DataFrame([{
        "Priority %": f["priority"]["percent"], "Band": f["priority"]["band"], "ID": f["finding_id"],
        "Title": f["title"], "Severity": f["severity"], "Profile": f["profile_label"],
        "Operation": f["operation"], "Evidence": f["evidence_category"],
        "Seen": f"{f['occurrences']} / {f['runs_in_same_situation']} runs",
    } for f in findings])
    st.dataframe(summary, hide_index=True, width="stretch")

    ids = [f["finding_id"] for f in findings]
    by_id = {f["finding_id"]: f for f in findings}
    chosen = st.selectbox("Inspect a finding", ids, format_func=lambda fid: f"{fid} — {by_id[fid]['title']} "
                                                                          f"({by_id[fid]['profile_label']})")
    with st.container(border=True):
        show_finding(by_id[chosen])
        st.button("Reproduce this finding →", on_click=go_to_reproduce, args=(chosen,))


def page_reproduce(findings):
    page_header("Reproduce a finding", "Rerun the original configuration to check whether the same detector fires again.", "INVESTIGATE")
    if not findings:
        st.info("No findings to reproduce yet.")
        return
    by_id = {f["finding_id"]: f for f in findings}
    ids = list(by_id)
    wanted = st.session_state.get("repro_finding")
    chosen = st.selectbox("Finding", ids, index=ids.index(wanted) if wanted in ids else 0,
                          format_func=lambda fid: f"{fid} — {by_id[fid]['title']} ({by_id[fid]['profile_label']})")
    st.session_state["repro_finding"] = chosen
    finding = by_id[chosen]
    config = finding["original_config"]

    st.markdown(f"Original test: **{finding['original_test_id']}** · "
                f"{badge(CATEGORY_BADGES, finding['evidence_category'])}")

    valid, problem = True, None
    if not isinstance(config, dict):
        valid, problem = False, "the original test record has no configuration"
    else:
        try:
            tester.validate_config(config)
        except ValueError as error:
            valid, problem = False, str(error)
    if not valid:
        st.error(f"The original configuration is unavailable ({problem}). Re-running is disabled so that "
                 "BudgetBench never silently runs a different test.")
        return

    params = pd.DataFrame([
        ("Operation", finding["operation_label"]), ("Profile", finding["profile_label"]),
        ("Client timeout (s)", config["timeout"]), ("Configured server delay (s)", config["server_delay"]),
        ("Injected HTTP 503 failures", config["fail_count"]), ("Client retries", config["retries"]),
        ("SIMULATED loss rate", config["loss_rate"]), ("Seed", config["seed"]),
        ("Upload size (KB)", config["upload_size_kb"] if config["operation"] == "upload" else "—"),
        ("Server de-duplication", config["idempotent_server"] if config["operation"] == "upload" else "—"),
        ("Demo app address", config["base_url"]),
    ], columns=["Parameter", "Original value"])
    params["Original value"] = params["Original value"].astype(str)

    left, right = st.columns([3, 2])
    left.dataframe(params, hide_index=True, width="stretch")
    right.markdown(f"Expected operation status  \n{badge(STATUS_BADGES, finding['original_expected_status'])}")
    right.markdown(f"Observed operation status  \n{badge(STATUS_BADGES, finding['original_observed_status'])}")
    right.markdown(f"Detector  \n`{finding['detector_rule']}`")

    st.markdown("**Steps**")
    st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(finding["reproduction_steps"], start=1)))
    st.markdown("**Same test from the terminal** (run inside the project folder with the virtual environment active)")
    st.code(finding["reproduction_command"], language="powershell")
    if finding["curl_commands"]:
        st.markdown("**Without BudgetBench** (one attempt, plain `curl.exe` in PowerShell)")
        st.code("\n".join(finding["curl_commands"]), language="powershell")
        st.caption("Use a new fault_key / upload_id (e.g. repro-2) each time: the demo app remembers them.")
    else:
        st.caption("No curl equivalent: this condition is SIMULATED and only exists inside BudgetBench.")

    if st.button("Re-run original configuration", type="primary"):
        ok, message = tester.check_server(config["base_url"])
        if not ok:
            st.error(message)
        else:
            with st.spinner("Re-running with the original settings — waiting for real replies..."):
                reproduced = tester.run_test(config)
                db_manager.index_run(reproduced, st.session_state.get("current_project"))
                st.session_state["repro_result"] = (chosen, reproduced)

    result = st.session_state.get("repro_result")
    if result and result[0] == chosen:
        record = result[1]
        if chosen in record["finding_ids"]:
            st.success(f"Reproduced: detector fired again in {record['test_id']}.")
        else:
            st.warning(f"Not reproduced in {record['test_id']}: the same detector did not fire this time.")
        show_record(record)


def page_reports(records, findings, assumptions):
    page_header("Reports", "Export saved runs and findings to share evidence with your team.", "SHARE RESULTS")
    st.caption(report_generator.DISCLAIMER)
    if not records:
        st.info("No test results yet, so there is nothing to export.")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    st.markdown(f"Exports contain **{len(records)} real test run(s)** and **{len(findings)} finding(s)**.")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("JSON report (everything)", report_generator.full_json(records, findings, assumptions),
                       file_name=f"budgetbench_report_{stamp}.json", mime="application/json")
    c2.download_button("Test results (CSV)", report_generator.results_csv(records),
                       file_name=f"budgetbench_results_{stamp}.csv", mime="text/csv")
    c3.download_button("Findings (CSV)", report_generator.findings_csv(findings),
                       file_name=f"budgetbench_findings_{stamp}.csv", mime="text/csv")
    markdown = report_generator.findings_markdown(findings, assumptions)
    c4.download_button("Findings report (Markdown)", markdown,
                       file_name=f"budgetbench_findings_{stamp}.md", mime="text/markdown")
    with st.expander("Preview the findings report"):
        st.markdown(markdown)

    st.divider()
    st.markdown("**Start a fresh session**")
    sure = st.checkbox("I understand this deletes all saved test results")
    if st.button("Clear all results", disabled=not sure):
        storage.clear_results()
        st.session_state.pop("last_run", None)
        st.session_state.pop("repro_result", None)
        st.rerun()


def page_projects():
    page_header("Projects", "Keep applications, environments, and test history organized in one workspace.", "WORKSPACE")
    with st.expander("Create a project", expanded=not bool(db_manager.list_projects())):
        with st.form("new_project"):
            name = st.text_input("Project name", placeholder="Checkout reliability")
            description = st.text_area("Description")
            cols = st.columns(3)
            target_type = cols[0].selectbox("Target type", ["Android application", "REST API", "Website", "Progressive Web Application"])
            environment = cols[1].selectbox("Environment", ["Development", "Staging", "Production"])
            package = cols[2].text_input("Android package (optional)")
            target_url = st.text_input("Target URL or API base URL", placeholder="https://api.example.com")
            submitted = st.form_submit_button("Create project", type="primary")
        if submitted:
            if len(name.strip()) < 2:
                st.error("Enter a project name with at least two characters.")
            else:
                pid = db_manager.save_project(name, description, target_type, target_url, package, environment)
                st.session_state["current_project"] = pid
                st.success(f"Project {pid} created.")
                st.rerun()
    query = st.text_input("Search projects", key="project_search")
    projects = db_manager.list_projects(query)
    if not projects:
        st.info("No projects match this search. Create one above to start organizing runs.")
        return
    for project in projects:
        with st.container(border=True):
            left, right = st.columns([5, 1])
            left.subheader(project["name"])
            left.caption(f"{project['project_id']} · {project['target_type']} · {project['environment']} · {project['target_url'] or 'No target URL'}")
            left.write(project["description"] or "No description added.")
            if right.button("Use project", key=f"use_{project['project_id']}"):
                st.session_state["current_project"] = project["project_id"]
                st.success(f"Selected {project['name']} for new test runs.")
            with st.expander("Project actions"):
                if st.button("Duplicate project", key=f"dup_{project['project_id']}"):
                    db_manager.save_project(project["name"] + " copy", project["description"], project["target_type"], project["target_url"], project["package_name"], project["environment"])
                    st.rerun()
                confirm = st.checkbox("Confirm permanent deletion", key=f"confirm_{project['project_id']}")
                if st.button("Delete project", disabled=not confirm, key=f"del_{project['project_id']}"):
                    db_manager.delete_project(project["project_id"])
                    st.session_state.pop("current_project", None)
                    st.rerun()


def page_device_lab():
    page_header("Device lab", "Connect an authorized Android device to collect real, read-only system snapshots.", "DEVICE TESTING")
    if adb_controller.adb_path():
        try:
            st.success(adb_controller.adb_version())
            devices = adb_controller.list_devices()
        except adb_controller.ADBError as error:
            st.error(str(error)); devices = []
    else:
        st.warning("ADB is not installed or is not on PATH. Install Android Platform Tools, enable USB debugging, authorize this computer, then refresh.")
        devices = []
    if st.button("Refresh devices"):
        st.rerun()
    if not devices:
        st.info("No ADB devices detected. This page remains usable without a phone; connect an authorized device to collect real measurements.")
    else:
        labels = [f"{d['model_hint'] or 'Android device'} · {d['state']} · {d['serial']}" for d in devices]
        chosen = st.selectbox("Connected devices", range(len(devices)), format_func=lambda i: labels[i])
        device = devices[chosen]
        if device["state"] != "device":
            st.warning(f"Device state is {device['state']}. Unlock the phone and approve the USB debugging prompt.")
        elif st.button("Collect device snapshot", type="primary"):
            try:
                with st.spinner("Reading device properties and system diagnostics…"):
                    details = adb_controller.collect_snapshot(device["serial"])
                    db_manager.save_device_snapshot(device["serial"], details)
                st.session_state["device_snapshot"] = details
                st.success("Device snapshot saved.")
            except adb_controller.ADBError as error:
                st.error(f"Could not collect the snapshot: {error}")
        details = st.session_state.get("device_snapshot")
        if details:
            st.subheader(f"{details.get('manufacturer') or 'Unknown manufacturer'} {details.get('model') or 'Android device'}")
            labels_map = [("Android", "android_version", ""), ("SDK", "sdk_version", ""),
                          ("System RAM", "ram_total_bytes", "bytes"), ("Available RAM", "ram_available_bytes", "bytes"),
                          ("/data free", "data_storage_available_bytes", "bytes"), ("Battery", "battery_percent", "%"),
                          ("Battery temperature", "battery_temperature_c", "°C"), ("CPU cores", "cpu_cores", "")]
            cols = st.columns(4)
            for index, (label, key, unit) in enumerate(labels_map):
                value = details.get(key)
                shown = f"{value:,} {unit}" if isinstance(value, (int, float)) and unit == "bytes" else (f"{value}{unit}" if value is not None else "Unavailable")
                cols[index % 4].metric(label, shown)
            st.caption("RAM is system-wide; /data is filesystem capacity rather than user-visible storage. Battery/thermal values depend on manufacturer support.")
    history = db_manager.list_snapshots()
    st.subheader("Saved device snapshots")
    if history:
        st.dataframe(pd.DataFrame([{**{k: row[k] for k in ("snapshot_id", "collected_at", "manufacturer", "model", "android_version")},
                                    "Evidence fields": len(__import__('json').loads(row['evidence_json']))} for row in history]), hide_index=True, width="stretch")
    else:
        st.caption("Snapshots collected on this installation will appear here.")


def page_test_environment():
    page_header("Test environment", "Choose a project and inspect the configuration and evidence type before running.", "CONFIGURE")
    projects = db_manager.list_projects()
    if projects:
        labels = {p["project_id"]: p["name"] for p in projects}
        ids = list(labels)
        selected = st.selectbox("Project", ids, index=ids.index(st.session_state.get("current_project")) if st.session_state.get("current_project") in ids else 0, format_func=lambda i: labels[i])
        st.session_state["current_project"] = selected
    profiles = list(tester.PROFILES.items())
    key = st.selectbox("Profile", [k for k, _ in profiles], format_func=lambda k: tester.PROFILES[k]["label"])
    profile = tester.PROFILES[key]
    st.markdown(profile["description"])
    st.info(f"Evidence: {tester.CATEGORY_LABELS[profile['evidence_category']]}. {profile['what_happens']}")
    st.json(profile["defaults"])
    if key == "packet_loss":
        st.warning("Packet loss is a deterministic software simulation; it does not shape the phone or network.")
    if key in ("normal", "slow_network", "intermittent", "packet_loss"):
        st.button("Configure this profile in Run Tests", type="primary",
                  on_click=navigate_to_profile, args=(key,))
    st.subheader("Not available in this build")
    st.write("Real network shaping, low-storage/memory pressure, native app journeys, process interruption and thermal throttling require additional device or emulator instrumentation. No controls claim these actions have been performed.")


def page_history(records):
    page_header("Test history", "Search saved runs and open the complete request, evidence, and configuration details.", "ACTIVITY")
    query = st.text_input("Filter by profile, status or test ID")
    filtered = [r for r in reversed(records) if query.lower() in (r["test_id"] + r["profile_label"] + r["operation_status"]).lower()]
    if not filtered:
        st.info("No saved runs match this filter."); return
    st.dataframe(pd.DataFrame([{"Test ID":r["test_id"],"Time":r["timestamp"],"Profile":r["profile_label"],"Operation":r["operation_label"],"Result":r["operation_status"],"Verdict":r["verdict"],"Evidence":r["evidence_category"],"Duration (s)":r["duration_s"]} for r in filtered]), hide_index=True, width="stretch")
    choice = st.selectbox("Open run details", [r["test_id"] for r in filtered])
    show_record(next(r for r in filtered if r["test_id"] == choice))


def page_failure_analysis(records, findings):
    page_header("Failure analysis", "See how observed outcomes and rule-based findings are distributed across recorded runs.", "ANALYTICS")
    st.caption("Deterministic rule-based detections from recorded test evidence; likelihood weights remain team assumptions.")
    if not records:
        st.info("Run a test to build evidence for failure analysis."); return
    categories = {}
    for r in records:
        categories[r["operation_status"]] = categories.get(r["operation_status"], 0) + 1
    st.bar_chart(pd.Series(categories, name="Runs"))
    if findings:
        st.dataframe(pd.DataFrame([{"Finding":f["title"],"Severity":f["severity"],"Priority":f["priority"]["percent"],"Occurrences":f["occurrences"],"Evidence":f["evidence_category"]} for f in findings]).sort_values("Priority", ascending=False), hide_index=True, width="stretch")
    st.caption("No user-population estimates are inferred from local test counts.")


def page_live_monitoring():
    page_header("Live monitoring", "Inspect device snapshots during a test; continuous sampling is not available in this build.", "DEVICE TESTING")
    st.info("This build provides on-demand device snapshots in Device Lab. Continuous sampling has not been implemented, so no live metrics are presented as if collected.")
    st.button("Open Device Lab", on_click=navigate_to, args=("Device Lab",))


def page_challenge():
    page_header("Challenge readiness", "Track the build plan, customer interviews, and evidence your team has actually collected.", "HACKFRONT INDIA")
    st.caption("Track actual preparation. Entries are stored locally in SQLite; no external validation is presumed.")
    plan = db_manager.load_challenge("build_plan", {})
    with st.form("build_plan"):
        status = st.text_area("Current prototype status", plan.get("status", ""))
        missing = st.text_area("Mocked or missing features", plan.get("missing", ""))
        customer = st.text_input("Primary customer segment", plan.get("customer", "Developers and QA engineers"))
        integration = st.text_input("Selected partner technology and need", plan.get("integration", ""))
        done = st.text_area("Definition of done", plan.get("done", ""))
        save = st.form_submit_button("Save build plan", type="primary")
    if save:
        db_manager.save_challenge("build_plan", {"status":status,"missing":missing,"customer":customer,"integration":integration,"done":done})
        st.success("Build plan saved.")
    st.subheader("Three-customer interview tracker")
    interviews = db_manager.load_challenge("interviews", [])
    with st.form("interview"):
        date = st.date_input("Interview date")
        segment = st.selectbox("Customer segment", ["Developer / QA", "QA lead / Engineering manager", "Agency / startup founder"])
        method = st.selectbox("Method", ["In person", "Video call", "Phone", "Async questionnaire"])
        assumption = st.text_input("Initial assumption")
        discovery = st.text_area("What did you discover?")
        evidence = st.text_input("Evidence / source")
        change = st.text_input("Product change")
        interview_save = st.form_submit_button("Record interview")
    if interview_save:
        if not discovery.strip(): st.error("Add a discovery before saving the interview.")
        else:
            interviews.append({"date":str(date),"segment":segment,"method":method,"assumption":assumption,"discovery":discovery,"evidence":evidence,"change":change})
            db_manager.save_challenge("interviews", interviews); st.success("Interview saved.")
    st.metric("Interviews recorded", len(interviews))
    if interviews: st.dataframe(pd.DataFrame(interviews), hide_index=True, width="stretch")
    st.subheader("Persona use case matrix")
    st.dataframe(pd.DataFrame([
      {"Segment":"Developers and QA Engineers","Problem":"Failures under constrained conditions","Use case":"Run tests and reproduce issues","Supporting feature":"HTTP harness, findings and reproduction","Status":"Implemented for demo HTTP target"},
      {"Segment":"QA Leads and Engineering Managers","Problem":"Compare reliability across runs","Use case":"Review history and priority","Supporting feature":"History, findings and analysis","Status":"Partially implemented"},
      {"Segment":"Agencies and Startup Founders","Problem":"Share clear client evidence","Use case":"Generate test reports","Supporting feature":"JSON/CSV/Markdown exports","Status":"Implemented for recorded tests"},
    ]), hide_index=True, width="stretch")
    st.warning("ArgosX baseline and final scan reports have not been uploaded. Do not mark security scanning complete without evidence.")


def page_graph_explorer():
    page_header("Graph explorer", "Explore related failures when a verified Neo4j integration is available.", "INTEGRATIONS")
    configured = all(os.environ.get(key) for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"))
    if not configured:
        st.info("Neo4j is not configured. Set NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD in the environment to enable graph storage.")
        return
    st.warning("Credentials are present, but this build does not yet verify a Neo4j connection or write graph relationships. No graph is shown as active.")


def page_settings():
    page_header("Settings", "Review this installation’s data location and local testing boundaries.", "PREFERENCES")
    st.write("Interface theme: **Dark** (the current supported theme).")
    st.code(f"SQLite database: {db_manager.database_path()}")
    st.write("ADB is read-only in Device Lab. The HTTP harness only targets its configured demo server in the current build.")


# ---------------------------------------------------------------- layout

db_manager.initialize()
style_path = Path(__file__).parent / "assets" / "style.css"
if style_path.exists():
    st.markdown(f"<style>{style_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

# Allow direct links such as http://localhost:8501/?page=Findings
if "page" not in st.session_state and st.query_params.get("page") in PAGES:
    st.session_state["page"] = st.query_params["page"]

with st.sidebar:
    st.markdown(
        '<div class="bb-brand"><div class="bb-brand-mark">B</div><div>'
        '<div class="bb-brand-name">BudgetBench</div>'
        '<div class="bb-brand-caption">ANDROID RELIABILITY LAB</div></div></div>',
        unsafe_allow_html=True)
    st.markdown('<div class="bb-nav-label">Workspace</div>', unsafe_allow_html=True)
    page = st.radio("Navigation", PAGES, format_func=lambda value: NAV_LABELS.get(value, value),
                    key="page", label_visibility="collapsed")
    projects = db_manager.list_projects()
    if projects:
        labels = {p["project_id"]: p["name"] for p in projects}
        ids = list(labels)
        st.markdown('<div class="bb-nav-label">Active project</div>', unsafe_allow_html=True)
        selected_id = st.selectbox("Active project", ids,
            index=ids.index(st.session_state.get("current_project")) if st.session_state.get("current_project") in ids else 0,
            format_func=lambda pid: labels[pid], label_visibility="collapsed")
        st.session_state["current_project"] = selected_id
    st.divider()
    st.markdown('<div class="bb-nav-label">Demo target</div>', unsafe_allow_html=True)
    if ONLINE_MODE:
        # Locked: on a public website, a free-text address would let anyone make our server
        # send requests to any address on the internet.
        base_url = start_builtin_demo_app()
        st.text_input("Demo app address", value=base_url, disabled=True)
        st.caption("**Online version.** The demo app runs inside this same server, and tests still send "
                   "real HTTP requests to it. Results are shared by everyone using this link and are "
                   "wiped when the app restarts.")
    else:
        base_url = st.text_input("Demo app address", value=tester.DEFAULT_BASE_URL)
        parsed = urlsplit(base_url)
        try:
            host_is_loopback = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            host_is_loopback = False
        if parsed.scheme not in ("http", "https") or not host_is_loopback:
            st.error("For this local demo build, the target must be localhost or a loopback IP. Remote URL testing is not enabled.")
            base_url = tester.DEFAULT_BASE_URL
    server_ok, server_message = tester.check_server(base_url)
    if server_ok:
        st.success(server_message)
    else:
        st.error(server_message)

records, warning, assumptions, findings = load_everything()
if warning:
    st.warning(warning)

if page in ("Dashboard", "Overview Dashboard"):
    page_dashboard(records, findings)
elif page == "Projects":
    page_projects()
elif page == "Device Lab":
    page_device_lab()
elif page == "Test Environment":
    page_test_environment()
elif page == "Live Monitoring":
    page_live_monitoring()
elif page == "Condition Matrix":
    page_condition_matrix(records)
elif page == "Run Tests":
    page_run_tests(base_url)
elif page == "Failure Analysis":
    page_failure_analysis(records, findings)
elif page == "Findings":
    page_findings(findings, assumptions)
elif page == "Reproduce":
    page_reproduce(findings)
elif page == "Test History":
    page_history(records)
elif page == "Neo4j Graph Explorer":
    page_graph_explorer()
elif page == "Challenge Readiness Centre":
    page_challenge()
elif page == "Settings":
    page_settings()
else:
    page_reports(records, findings, assumptions)

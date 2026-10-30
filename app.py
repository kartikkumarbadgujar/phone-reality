"""
app.py - the PhoneReality dashboard (Streamlit).

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
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import demo_app
import detectors
import report_generator
import scoring
import storage
import tester

st.set_page_config(page_title="PhoneReality", page_icon="📱", layout="wide")

PAGES = ["Dashboard", "Condition Matrix", "Run Tests", "Findings", "Reproduce", "Reports"]

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
    assumptions = storage.load_assumptions(scoring.DEFAULT_ASSUMPTIONS)
    findings = detectors.build_findings(records, assumptions)
    return records, warning, assumptions, findings


def go_to_reproduce(finding_id):
    """Button callback: jump to the Reproduce page with this finding selected."""
    st.session_state["page"] = "Reproduce"
    st.session_state["repro_finding"] = finding_id


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
    st.header("Dashboard")
    if not records:
        st.info("No tests have been run yet, so there is nothing to show. "
                "Nothing on this page is sample data — it only ever shows real runs.")
        st.markdown("1. Start the demo app in a terminal: `python demo_app.py`\n"
                    "2. Open **Run Tests**, choose *Upload a file* + *Slow Network*, click **Run test**.\n"
                    "3. Come back here, or open **Findings**.")
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
               "**Harness verdict** = whether PhoneReality reproduced the outcome it predicted from the settings. "
               "A deliberately caused timeout that is observed as predicted counts as a verdict PASS.")

    by_category = {c: sum(r["evidence_category"] == c for r in records) for c in CATEGORY_BADGES}
    c1, c2, c3 = st.columns(3)
    c1.metric("Genuine measurements", by_category["GENUINE"])
    c2.metric("Controlled fault injections", by_category["FAULT_INJECTION"])
    c3.metric("SIMULATED", by_category["SIMULATED"])

    st.subheader("Operation outcome by profile")
    table = pd.DataFrame([{"Profile": tester.PROFILES[r["profile"]]["short"], "Status": r["operation_status"]}
                          for r in records])
    counts = table.groupby(["Profile", "Status"]).size().unstack(fill_value=0)
    statuses = [s for s in STATUS_COLORS if s in counts.columns]
    # In a horizontal bar chart Streamlit puts x_label on the category axis and y_label on the value axis.
    st.bar_chart(counts[statuses], color=[STATUS_COLORS[s] for s in statuses], horizontal=True,
                 x_label="", y_label="Number of tests", height=90 + 45 * len(counts))

    st.subheader("Recent activity")
    recent = pd.DataFrame([{
        "Time": r["timestamp"][11:], "Test ID": r["test_id"], "Profile": r["profile_label"],
        "Operation": r["operation"], "Evidence": r["evidence_category"], "Expected": r["expected_status"],
        "Actual": r["operation_status"], "Verdict": r["verdict"], "Duration (s)": r["duration_s"],
        "Findings": ", ".join(r["finding_ids"]) or "—",
    } for r in reversed(records[-15:])])
    st.dataframe(recent, hide_index=True, width="stretch")


def page_condition_matrix(records):
    st.header("Condition Matrix")
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
    st.header("Run Tests")
    c1, c2 = st.columns(2)
    operation = c1.selectbox("Operation", list(tester.OPERATIONS),
                             format_func=lambda k: tester.OPERATIONS[k]["label"])
    profile = c2.selectbox("Condition profile", list(tester.PROFILES),
                           format_func=lambda k: tester.PROFILES[k]["label"])
    info = tester.PROFILES[profile]
    st.markdown(f"{badge(CATEGORY_BADGES, info['evidence_category'])} {info['description']}")

    defaults = tester.build_config(operation, profile, base_url)
    key = f"{operation}_{profile}"  # new widget keys per combination, so defaults reload when you switch
    is_upload = operation == "upload"

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
    st.markdown(f"Evidence category for these settings: {badge(CATEGORY_BADGES, tester.evidence_category(config))}"
                f" · Predicted operation status: {badge(STATUS_BADGES, predicted)}")
    st.caption(f"Prediction: {reason}")
    if predicted == "UNPREDICTABLE":
        st.warning(reason)

    if st.button("Run test", type="primary"):
        ok, message = tester.check_server(base_url)
        if not ok:
            st.error(message)
        else:
            results = []
            progress = st.progress(0.0, text="Starting...")
            for i in range(int(repeat)):
                progress.progress(i / repeat, text=f"Running test {i + 1} of {repeat} — waiting for real replies...")
                results.append(tester.run_test(config))
            progress.progress(1.0, text=f"Finished {repeat} test(s).")
            st.session_state["last_run"] = results

    for record in st.session_state.get("last_run", []):
        show_record(record)


def page_findings(findings, assumptions):
    st.header("Findings")
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
    st.header("Reproduce")
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
                 "PhoneReality never silently runs a different test.")
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
        st.markdown("**Without PhoneReality** (one attempt, plain `curl.exe` in PowerShell)")
        st.code("\n".join(finding["curl_commands"]), language="powershell")
        st.caption("Use a new fault_key / upload_id (e.g. repro-2) each time: the demo app remembers them.")
    else:
        st.caption("No curl equivalent: this condition is SIMULATED and only exists inside PhoneReality.")

    if st.button("Re-run original configuration", type="primary"):
        ok, message = tester.check_server(config["base_url"])
        if not ok:
            st.error(message)
        else:
            with st.spinner("Re-running with the original settings — waiting for real replies..."):
                st.session_state["repro_result"] = (chosen, tester.run_test(config))

    result = st.session_state.get("repro_result")
    if result and result[0] == chosen:
        record = result[1]
        if chosen in record["finding_ids"]:
            st.success(f"Reproduced: detector fired again in {record['test_id']}.")
        else:
            st.warning(f"Not reproduced in {record['test_id']}: the same detector did not fire this time.")
        show_record(record)


def page_reports(records, findings, assumptions):
    st.header("Reports")
    st.caption(report_generator.DISCLAIMER)
    if not records:
        st.info("No test results yet, so there is nothing to export.")
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    st.markdown(f"Exports contain **{len(records)} real test run(s)** and **{len(findings)} finding(s)**.")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("JSON report (everything)", report_generator.full_json(records, findings, assumptions),
                       file_name=f"phonereality_report_{stamp}.json", mime="application/json")
    c2.download_button("Test results (CSV)", report_generator.results_csv(records),
                       file_name=f"phonereality_results_{stamp}.csv", mime="text/csv")
    c3.download_button("Findings (CSV)", report_generator.findings_csv(findings),
                       file_name=f"phonereality_findings_{stamp}.csv", mime="text/csv")
    markdown = report_generator.findings_markdown(findings, assumptions)
    c4.download_button("Findings report (Markdown)", markdown,
                       file_name=f"phonereality_findings_{stamp}.md", mime="text/markdown")
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


# ---------------------------------------------------------------- layout

# Allow direct links such as http://localhost:8501/?page=Findings
if "page" not in st.session_state and st.query_params.get("page") in PAGES:
    st.session_state["page"] = st.query_params["page"]

with st.sidebar:
    st.title("PhoneReality")
    st.caption("Test beyond ideal conditions.")
    page = st.radio("Go to", PAGES, key="page")
    st.divider()
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
    server_ok, server_message = tester.check_server(base_url)
    if server_ok:
        st.success(server_message)
    else:
        st.error(server_message)

records, warning, assumptions, findings = load_everything()
if warning:
    st.warning(warning)

if page == "Dashboard":
    page_dashboard(records, findings)
elif page == "Condition Matrix":
    page_condition_matrix(records)
elif page == "Run Tests":
    page_run_tests(base_url)
elif page == "Findings":
    page_findings(findings, assumptions)
elif page == "Reproduce":
    page_reproduce(findings)
else:
    page_reports(records, findings, assumptions)

# BudgetBench

**Test Beyond the Flagship. Build for Every Phone.**

BudgetBench is a local Streamlit reliability workbench for HackFront India PS T1.2. It retains the original PhoneReality HTTP testing engine and its real demo backend, retry behavior, eight rule-based detectors, transparent scoring, reproduction recipes, and JSON/CSV/Markdown reporting. It adds persistent SQLite projects, an ADB Device Lab for real read-only device snapshots, project-linked run history, failure analysis, and a Challenge Readiness Centre.

## Run locally

Requires Python 3.11 or newer. On Windows, install Android Platform Tools if you want to use the Device Lab.

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the demonstration backend in one terminal:

```powershell
python demo_app.py
```

Start BudgetBench in another terminal:

```powershell
streamlit run app.py --server.address localhost
```

Open `http://localhost:8501`. The built-in server exercises genuine HTTP requests against the local demo target. The dashboard will report when the server is unavailable.

For Android snapshots, enable USB debugging, connect and authorize the phone, then verify with `adb devices`. Device Lab uses bounded, read-only ADB commands and labels missing readings as unavailable. System RAM is not app-specific RAM; `/data` storage reflects a filesystem measurement.

## Main workflow

1. Create a project and select it in the sidebar.
2. Inspect a saved profile in Test Environment or open Run Tests directly.
3. Configure an HTTP scenario and review its predicted outcome and evidence type.
4. Run the test and inspect attempts, server receipts and rule-based findings.
5. Reproduce a finding with its stored configuration or export the report.
6. Use Device Lab to collect an actual Android snapshot when a phone is connected.

The most useful demo is the ambiguous upload: the server saves a file, delays its response beyond the client timeout, and exposes server receipts. A retry can create duplicate copies; enabling server idempotency demonstrates the fix. Packet loss is explicitly simulated, while delay and HTTP 503 are controlled server faults.

## Data and privacy

- Run records from the original engine remain in `data/results.json` for compatibility.
- Projects, device snapshots, and an index of application-run sessions are stored in `data/budgetbench.sqlite3` using SQLite WAL mode.
- Set `PHONEREALITY_DATA_DIR` to relocate legacy engine data, or `BUDGETBENCH_DB` to relocate SQLite.
- Device serial identifiers are used internally to associate snapshots. The UI only shows the serial in the connected-device picker; reports from the legacy engine do not contain device serials.
- The demo backend is intended for local evaluation, not production deployment.
- The demo backend accepts loopback clients only, rejects browser-origin requests, caps each request at 6 MiB, and limits stored uploads to 256 MiB / 1,000 receipts.
- Hosted mode requires `BUDGETBENCH_ACCESS_KEY` in Streamlit secrets (or the deployment environment). Do not publish a hosted instance without configuring this key. It is a shared workspace key, not individual user accounts.

## Evidence limits

The HTTP harness executes genuine requests against a local demo server. It does not measure Indian mobile networks or claim a physical phone was throttled. The ADB module reads device properties and system diagnostics; it does not automate native app journeys or collect app-specific memory yet. Continuous monitoring, network shaping, memory/storage pressure, native UI automation, Playwright journeys, PDF reports, Neo4j graph writes, individual accounts, and multi-user roles are not implemented in this build. The corresponding pages state these limits rather than presenting simulated results as real evidence.

The challenge tracker stores team-entered plans and interviews locally. It does not claim customer validation or a security scan unless the team records evidence.

## Run the existing test suite

```powershell
python -m pytest -q
```

The original PhoneReality suite exercises detector rules, storage, scoring, report generation, the real HTTP demo backend, reproduction, and Streamlit pages. Three tests were added for SQLite persistence and ADB evidence parsing. The full post-change suite passes: **66 passed**.

## Project map

- `app.py` — Streamlit UI and page routing.
- `tester.py`, `demo_app.py` — preserved HTTP harness and controlled-fault demo server.
- `detectors.py`, `scoring.py`, `report_generator.py` — rule-based analysis and exports.
- `core/adb_controller.py` — safe ADB detection and snapshot collection.
- `database/db_manager.py` — SQLite projects, snapshots, challenge inputs, and test-session index.
- `assets/style.css` — BudgetBench visual system.
- `tests/` — original 63-test suite.

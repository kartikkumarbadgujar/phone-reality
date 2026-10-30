# PhoneReality

**Test beyond ideal conditions.**

PhoneReality is a small testing tool built for HackFront India problem statement **T1.2 — The Cheap Phone Test**.
It runs real HTTP tests against a demo app under deliberately bad conditions (slow replies, server errors,
simulated packet loss), detects failures with simple fixed rules, captures evidence, ranks findings by a
transparent priority score, and tells another developer exactly how to reproduce each one.

> **Honesty note.** Everything runs on one laptop. PhoneReality measures how *software* behaves when a
> request is slow or fails. It does **not** measure real Indian mobile networks or real Android phones.
> Every result is labelled with its evidence category: **GENUINE**, **CONTROLLED FAULT INJECTION** or **SIMULATED**.

---

## The main demonstration (the "ambiguous upload")

1. The phone uploads a file.
2. The server **saves the file**, then (deliberately) waits 3 seconds before replying.
3. The phone gives up after 1 second: a real `requests.exceptions.ReadTimeout`.
4. The app would now show "Upload failed" — but PhoneReality asks the server and finds the file **was saved**.
5. If the app retries (retries = 1), the server saves it **again**: a real duplicate.
6. Tick *"Server de-duplicates by upload_id (the fix)"* and run again: the duplicate disappears.

On a slow network this is how users end up with double posts, double orders or double payments.

---

## First-time setup (Windows, VS Code)

You only do this once.

1. **Check Python.** Open PowerShell and run:

   ```powershell
   py --version
   ```

   You should see `Python 3.14.x` (3.11 or newer should also work, but 3.14 is what was tested).
   Use `py`, not `python`: on many Windows laptops `python` opens the Microsoft Store instead.

2. **Open the project in VS Code.** *File → Open Folder…* → choose the `phone_reality` folder.

3. **Open the terminal inside VS Code.** *Terminal → New Terminal* (or press <kbd>Ctrl</kbd>+<kbd>`</kbd>).
   It opens in the project folder.

4. **Create a virtual environment** (a private folder `.venv` holding this project's libraries, so they don't
   mix with anything else on your laptop):

   ```powershell
   py -m venv .venv
   ```

5. **Activate it** (tells this terminal to use the `.venv` Python). Windows blocks scripts by default, so first
   allow them **for this terminal window only** (safe — it resets when the window closes):

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .venv\Scripts\Activate.ps1
   ```

   Your prompt now starts with `(.venv)`. From now on `python` means the project's Python.
   **Repeat these two lines in every new terminal.** (Tested on this laptop: without the first line,
   activation fails with *"running scripts is disabled on this system"*.)

6. **Install the libraries:**

   ```powershell
   python -m pip install -r requirements.txt
   ```

---

## Running PhoneReality

PhoneReality is **two programs** that talk to each other over HTTP:

| Program | What it is | Address |
|---|---|---|
| `demo_app.py` | The app being tested (a small server with switchable faults) | http://127.0.0.1:5050 |
| `app.py` | The PhoneReality dashboard | http://localhost:8501 |

### Option A — one click

Double-click **`start.bat`** in the project folder (after the first-time setup). It opens two windows and then
the dashboard in your browser. Close the two windows to stop.

### Option B — two terminals (recommended while developing)

**Terminal 1 — the demo app** (leave it running):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
python demo_app.py
```

**Terminal 2 — the dashboard** (click the **+** in the VS Code terminal panel to open a second terminal):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
streamlit run app.py --server.address localhost
```

(`--server.address localhost` means only your laptop can open the dashboard, not other people on the same Wi-Fi.)

Open **http://localhost:8501** (Ctrl+click the link printed in the terminal).
The sidebar shows a green box when the dashboard can reach the demo app.

To stop either program, click in its terminal and press <kbd>Ctrl</kbd>+<kbd>C</kbd>.

### Option C — online (Streamlit Community Cloud)

Streamlit Cloud runs only **one** program, so online the dashboard starts the demo app itself, inside the same
server (`demo_app.start_in_background`). Tests still send real HTTP requests to it over `127.0.0.1`.
Online mode switches on automatically on Streamlit Cloud, and it locks the "Demo app address" box. Otherwise
anyone on the internet could make the server send requests to any address.

To deploy:

1. Put this folder on GitHub (the `.venv` folder and your local results are excluded by `.gitignore`).
2. Go to **https://share.streamlit.io**, sign in with GitHub, choose **Create app → Deploy from GitHub**.
3. Pick the repository, branch `main`, main file **`app.py`**. Under *Advanced settings* choose the newest
   Python version offered. Click **Deploy** and wait a few minutes.

Things to know about the online version:

* Everyone with the link shares the same results, and anyone can press *Clear all results*.
* Results disappear whenever Streamlit restarts the app. Free apps also go to sleep when unused; the first
  visitor sees a *"wake up"* button and waits about a minute.
* For judging, the **local** version (Option A/B) is the safest: it needs no internet.

To try online mode on your laptop: `$env:PHONEREALITY_CLOUD="1"; streamlit run app.py`.

### Running a test from the terminal (no dashboard needed)

```powershell
python tester.py --operation upload --profile slow_network
python tester.py --operation upload --profile slow_network --retries 1
python tester.py --operation upload --profile slow_network --retries 1 --idempotent-server
```

Every finding's **Reproduce** page shows the exact command to repeat it.

### Running the automated tests

```powershell
python -m pytest
```

This starts its own copy of the demo app on a free port, so you do **not** need `demo_app.py` running.
It never touches your real `data/results.json`. Expected result: `63 passed`.

---

## Dashboard pages

| Page | What it is for |
|---|---|
| **Dashboard** | Totals, genuine vs fault-injection vs simulated counts, outcome chart, recent runs. Empty until you run a test — nothing is sample data. |
| **Condition Matrix** | Every profile, what it does, its evidence category and the latest real result. Not-implemented profiles are listed as future work (no buttons). |
| **Run Tests** | Pick operation + profile, adjust timeout / delay / failures / retries / loss / seed, see the predicted outcome, run. |
| **Findings** | Ranked findings with evidence, the full score calculation, and editable assumptions. |
| **Reproduce** | Original settings of a finding, step-by-step instructions, terminal and `curl.exe` commands, and a **Re-run original configuration** button. |
| **Reports** | Download JSON (everything), CSV (tests), CSV (findings) and a Markdown findings report. |

---

## Condition profiles

| Profile | What happens | Evidence category |
|---|---|---|
| Normal | Plain request / upload, no faults | Genuine local measurement |
| Slow Network | Server waits 3 s; client timeout is 1 s. Uploads are saved *before* the wait. | Controlled fault injection — the timeout is real |
| Intermittent Connectivity | First 2 requests get HTTP 503; client retries once | Controlled fault injection |
| Packet Loss | Seeded random drop of each attempt before it is sent | **SIMULATED** |
| Process Interruption | — | Not implemented (needs a real device / emulator) |
| Resource Pressure | — | Not implemented (needs device instrumentation) |
| Thermal / Battery | — | Not implemented (needs a physical device) |

The evidence category of each run is worked out from the **settings actually used**: any loss rate above 0 makes
the run SIMULATED; any delay or injected failure makes it FAULT_INJECTION; otherwise GENUINE.

---

## How it works

```
 ┌──────────────── your laptop ─────────────────────────────────────────────┐
 │                                                                          │
 │   Browser ──► app.py (Streamlit dashboard, port 8501)                    │
 │                  │  calls                                                │
 │                  ▼                                                       │
 │               tester.py ──── real HTTP requests ────► demo_app.py        │
 │                  │           (timeout, retries)        (Flask, port 5050)│
 │                  │                                     saves uploads to  │
 │                  │◄── "what did you really save?" ──── data/server_*     │
 │                  ▼                                                       │
 │               detectors.py  (fixed rules → detections → findings)        │
 │                  ▼                                                       │
 │               scoring.py    (Severity × Likelihood × Impact)             │
 │                  ▼                                                       │
 │               storage.py ──► data/results.json                           │
 │               report_generator.py ──► JSON / CSV / Markdown downloads    │
 └──────────────────────────────────────────────────────────────────────────┘
```

### The files

| File | Purpose |
|---|---|
| `demo_app.py` | The app under test. Endpoints: `/api/health`, `/api/data`, `/api/upload`, `/api/uploads/<id>`. Faults are switched on with query parameters: `delay`, `fail_count` + `fault_key`, `idempotent`. |
| `tester.py` | Builds a test configuration from a profile, **predicts** the outcome from the settings, sends the real requests, records every attempt, checks the server for uploads, and saves the record. Also a command-line tool. |
| `detectors.py` | Eight detectors, each a single readable rule (below). Groups detections into findings and writes reproduction steps. |
| `scoring.py` | The priority formula and the default (editable) assumptions. |
| `storage.py` | Reads/writes `data/results.json` and `data/assumptions.json`. Copes with missing, empty and broken files. |
| `report_generator.py` | JSON, CSV and Markdown exports. |
| `app.py` | The Streamlit dashboard (screens only — no testing logic). |
| `tests/` | 63 automated tests: unit tests plus end-to-end tests against a real demo app and the real dashboard. |
| `start.bat` | One-click start. |
| `.streamlit/config.toml` | Dashboard settings (no e-mail prompt, no usage statistics, colours). Used locally and online. |

### Two answers per test: operation status and verdict

| Field | Meaning | Example |
|---|---|---|
| `operation_status` | What the app experienced | `TIMEOUT` |
| `expected_status` | What PhoneReality predicted from the settings | `TIMEOUT` (delay 3 s ≥ timeout 1 s) |
| `verdict` | Did the harness reproduce its prediction? | `PASS` |

So *"operation TIMEOUT, verdict PASS"* means: we caused a timeout on purpose and observed exactly that.
A verdict **FAIL** means the harness saw something it did not predict — worth investigating.
**INCONCLUSIVE** is used when delay and timeout are within 0.3 s of each other (timing noise could decide).
**ERROR** means the demo app was unreachable; such runs are shown but **not saved**, because a stopped
server is a setup problem, not a finding.

### Detectors

| Detector | Rule | Severity |
|---|---|---|
| DUPLICATE_UPLOAD | upload AND server saved > 1 copy of one `upload_id` | 5 |
| UPLOAD_INTEGRITY | upload AND client got success AND (no server copy OR SHA-256 differs) | 5 |
| AMBIGUOUS_UPLOAD | upload AND client did not get success AND server saved ≥ 1 copy | 4 |
| UPLOAD_NOT_SAVED | upload AND client did not get success AND server saved 0 copies | 4 |
| RECOVERY_FAILURE | retries > 0 AND no attempt succeeded | 4 |
| TIMEOUT | final status TIMEOUT (`requests` raised `ReadTimeout`) | 3 |
| HTTP_FAILURE | final status was a non-2xx HTTP code | 3 |
| REQUEST_LOST_SIMULATED | final attempt dropped by the simulation | 2 |

Detectors are independent: one test can trigger several (a retried slow upload triggers TIMEOUT,
AMBIGUOUS_UPLOAD, DUPLICATE_UPLOAD and RECOVERY_FAILURE — four different problems with four different fixes).
Repeated runs of the same situation are grouped into **one** finding with an occurrence count.

### Priority score

```
Priority (raw) = Severity × Likelihood × Impact weight      (each 1–5, maximum 125)
Priority (%)   = raw ÷ 125 × 100        High ≥ 50 %, Medium ≥ 25 %, Low < 25 %
```

* **Severity** — fixed per detector type (table above).
* **Likelihood** — how often we *assume* users are in that condition. Defaults: Normal 5, Slow 4, Intermittent 3, Packet loss 3.
* **Impact weight** — how much the operation matters to the user. Defaults: Load data 3, Upload 5.

Likelihood and impact weight are **team assumptions, not measurements**, and are not taken from any survey.
They are editable on the Findings page and saved to `data/assumptions.json`. Our local runs cannot tell us how
common slow networks are among Indian phone users — that is why **measured frequency** ("detected in 3 of 3
runs") is shown separately and never mixed into the score.

Example: ambiguous upload on Slow Network → 4 × 4 × 5 = 80 / 125 = **64 % (High)**.

---

## What is real, what is injected, what is simulated

| | Examples | Is it real? |
|---|---|---|
| **Genuine measurement** | HTTP status codes, response times, `ReadTimeout` exceptions, file bytes, SHA-256 hashes, number of copies the server saved | Yes — real software behaviour on this laptop |
| **Controlled fault injection** | The server sleeping before it replies; the server answering 503 | The *fault* is deliberate; the client's *reaction* is genuinely observed |
| **Simulation** | Packet loss | No — a seeded random number decides; treat as a hypothesis |
| **Not done** | Real mobile networks, Android process death, memory/storage pressure, thermal throttling, battery saver | Listed as future work |

---

## Limitations

* Tests run against a demo app on `127.0.0.1`, not a real product backend and not a real phone.
* "Slow network" is modelled as a slow *server reply*. Real slow networks also slow the upload itself,
  lose packets mid-transfer and change speed over time.
* Packet loss is a simple per-attempt model; real loss happens per packet inside TCP.
* Likelihood and impact weights are assumptions (see above).
* The demo app's 503 counters live in memory: restarting `demo_app.py` resets them (each test uses its own key, so tests are unaffected).
* The Flask server is a development server — fine for a local demo, not for production.

## Future improvements

* Point PhoneReality at a real app's API (configurable endpoints) instead of only the demo app.
* Real network shaping (for example a throttling proxy, or Android emulator network speed settings) to replace injected delay.
* Android emulator / device runs via `adb` for process death, low storage and battery-saver tests.
* Use real analytics data (share of users on slow networks) to set likelihood instead of assumptions.
* Upload-progress tests (slow upload of large files, connection dropped half-way).

---

## Troubleshooting

**`py` or `python` is not recognised / opens the Microsoft Store**
Install Python from python.org (tick *"Add python.exe to PATH"*), then use `py`. Once the virtual environment is
activated, `python` works.

**"running scripts is disabled on this system" when activating**
Allow scripts **for this terminal window only** (safe; it resets when you close the window):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

Or skip activation and call the venv Python directly, e.g. `.venv\Scripts\python.exe demo_app.py` and
`.venv\Scripts\python.exe -m streamlit run app.py`.

**Sidebar says "Cannot reach the demo app"**
`demo_app.py` is not running, or is on another port. Start it in a separate terminal (`python demo_app.py`)
and check the address in the sidebar is `http://127.0.0.1:5050`.

**"Address already in use" / port 5050 or 8501 is busy**
Another copy is probably still running — close its terminal. Or use other ports:

```powershell
python demo_app.py --port 5051          # then type http://127.0.0.1:5051 in the dashboard sidebar
streamlit run app.py --server.port 8502 # then open http://localhost:8502
```

To see what is using a port: `Get-NetTCPConnection -LocalPort 5050 | Select-Object OwningProcess`.

**`ModuleNotFoundError: No module named 'streamlit'` (or flask / requests)**
The virtual environment is not active in this terminal. Activate it (prompt shows `(.venv)`) or reinstall:
`python -m pip install -r requirements.txt`.

**A warning says the results file could not be read**
`data/results.json` was damaged (for example edited by hand). PhoneReality renamed it to
`results.corrupt-<date>.json` and started a new empty file. Your old data is in the backup.

**The Windows Firewall asks about Python**
The demo app only listens on `127.0.0.1` (this laptop), so you can click *Cancel*/*Deny*.

**Starting fresh before a demo**
Reports page → tick *"I understand…"* → **Clear all results**. Uploaded test files are in `data/server_files/`
and can be deleted at any time.

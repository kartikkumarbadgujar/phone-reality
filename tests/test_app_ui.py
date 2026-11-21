"""Dashboard tests using Streamlit's built-in AppTest (runs app.py without a browser)."""

import pytest
from streamlit.testing.v1 import AppTest

import storage
from conftest import PROJECT

APP = str(PROJECT / "app.py")
PAGES = ["Dashboard", "Condition Matrix", "Run Tests", "Findings", "Reproduce", "Reports"]


def open_page(page, base_url="http://127.0.0.1:9"):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.sidebar.text_input[0].set_value(base_url)
    at.sidebar.radio[0].set_value(page)
    at.run()
    assert not at.exception, at.exception
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_works_with_no_results(page):  # T12
    at = open_page(page)
    assert storage.load_results() == ([], None)  # nothing fabricated


def test_empty_dashboard_says_so():
    at = open_page("Dashboard")
    assert any("No tests have been run yet" in i.value for i in at.info)
    assert [metric.value for metric in at.metric] == ["0", "0", "0", "0", "0"]


def test_sidebar_reports_server_down():
    at = open_page("Dashboard")
    assert any("Cannot reach the demo app" in e.value for e in at.sidebar.error)


def test_run_button_executes_real_test_and_fills_pages(demo_server):
    at = open_page("Run Tests", demo_server)
    at.selectbox[0].set_value("upload")
    at.selectbox[1].set_value("slow_network")
    at.run()
    at.number_input(key="timeout_upload_slow_network").set_value(0.5)
    at.number_input(key="delay_upload_slow_network").set_value(1.5)
    at.run()
    at.button[0].click()
    at.run()
    assert not at.exception, at.exception
    assert any("Client reported failure but the server saved the upload" in w.value for w in at.warning)

    records, _ = storage.load_results()
    assert len(records) == 1 and records[0]["operation_status"] == "TIMEOUT"

    for page in ["Dashboard", "Findings", "Reproduce", "Reports"]:
        at.sidebar.radio[0].set_value(page)
        at.run()
        assert not at.exception, (page, at.exception)
    dashboard = open_page("Dashboard", demo_server)
    assert next(metric.value for metric in dashboard.metric if metric.label == "Test sessions") == "1"


def test_online_mode_runs_builtin_demo_app_with_locked_address(monkeypatch):
    """Simulates Streamlit Cloud: no separate demo_app.py, and visitors cannot change the address."""
    monkeypatch.setenv("PHONEREALITY_CLOUD", "1")
    monkeypatch.setenv("PHONEREALITY_BUILTIN_PORT", "0")  # any free port, so it never clashes in tests
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    address = at.sidebar.text_input[0]
    assert address.disabled
    assert any("Demo app is running" in s.value for s in at.sidebar.success)

    at.sidebar.radio[0].set_value("Run Tests")
    at.run()
    at.selectbox[0].set_value("upload")
    at.run()
    at.button[0].click()
    at.run()
    assert not at.exception, at.exception
    records, _ = storage.load_results()
    assert records[0]["operation_status"] == "SUCCESS"
    assert records[0]["config"]["base_url"] == address.value
    assert records[0]["evidence"]["server_check"]["receipt_count"] == 1


def test_reproduce_rerun_uses_original_config(demo_server):
    at = open_page("Run Tests", demo_server)
    at.selectbox[1].set_value("slow_network")
    at.run()
    at.number_input(key="timeout_load_data_slow_network").set_value(0.5)
    at.number_input(key="delay_load_data_slow_network").set_value(1.5)
    at.run()
    at.button[0].click()
    at.run()

    at.sidebar.radio[0].set_value("Reproduce")
    at.run()
    rerun = next(b for b in at.button if b.label == "Re-run original configuration")
    rerun.click()
    at.run()
    assert not at.exception, at.exception
    assert any(s.value.startswith("Reproduced") for s in at.success)
    first, second = storage.load_results()[0]
    assert first["config"] == second["config"]

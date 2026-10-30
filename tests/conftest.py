"""Shared test setup: make the project importable, keep test data out of data/, start a real demo app."""

import socket
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import demo_app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own empty data folder, so your real results.json is never touched."""
    monkeypatch.setenv("PHONEREALITY_DATA_DIR", str(tmp_path / "phonereality"))
    return tmp_path / "phonereality"


@pytest.fixture(scope="session")
def demo_server(tmp_path_factory):
    """Start the real demo app on a free port in a background thread. Yields its base URL."""
    server, base_url = demo_app.start_in_background(tmp_path_factory.mktemp("server"), port=0)
    yield base_url
    server.shutdown()


@pytest.fixture
def free_port():
    """A port with nothing listening on it (for the 'server unavailable' test)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
